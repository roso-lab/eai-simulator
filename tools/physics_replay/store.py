"""Versioned NumPy storage for physics replay episodes."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any
import zipfile

import numpy as np

from .types import FORMAT_VERSION, FieldSchema, FrameRecord, Manifest


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _field_schema(array: np.ndarray) -> dict[str, Any]:
    return FieldSchema(tuple(array.shape), str(array.dtype)).to_dict()


def _copy_array(value: Any) -> np.ndarray:
    array = np.asarray(value)
    if array.dtype == object:
        raise ValueError("replay state and action arrays cannot have object dtype")
    return array.copy()


class EpisodeWriter:
    """Append fixed-shape frame records to an episode directory."""

    def __init__(self, root: str | Path, manifest: Manifest):
        self.root = Path(root)
        self.segments_dir = self.root / "segments"
        if (self.root / "manifest.json").exists() or (self.root / "index.json").exists():
            raise FileExistsError(f"replay episode already exists: {self.root}")
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = manifest
        self.manifest.status = "recording"
        self._buffer: list[FrameRecord] = []
        self._segments: list[dict[str, Any]] = []
        self._state_keys: tuple[str, ...] | None = None
        self._action_keys: tuple[str, ...] | None = None
        self._command_keys: tuple[str, ...] | None = None
        self._noise_keys: tuple[str, ...] | None = None
        self._applied_action_keys: tuple[str, ...] | None = None
        self._last_step: int | None = None
        self._closed = False
        self._write_manifest()

    def append(self, frame: FrameRecord) -> None:
        if self._closed:
            raise RuntimeError("cannot append to a closed replay episode")
        if self._last_step is not None and frame.step_index <= self._last_step:
            raise ValueError("frame step_index must increase strictly")
        if set(frame.state_before) != set(frame.state_after):
            raise ValueError("state_before and state_after must contain the same fields")

        state_keys = tuple(sorted(frame.state_before))
        action_keys = tuple(sorted(frame.actions))
        command_keys = tuple(sorted(frame.commands))
        noise_keys = tuple(sorted(frame.noise))
        applied_action_keys = tuple(sorted(frame.applied_actions))
        if self._state_keys is None:
            self._state_keys = state_keys
            self._action_keys = action_keys
            self._command_keys = command_keys
            self._noise_keys = noise_keys
            self._applied_action_keys = applied_action_keys
            self.manifest.metadata["state_keys"] = list(state_keys)
            self.manifest.metadata["action_keys"] = list(action_keys)
            self.manifest.metadata["command_keys"] = list(command_keys)
            self.manifest.metadata["noise_keys"] = list(noise_keys)
            self.manifest.metadata["applied_action_keys"] = list(applied_action_keys)
            self.manifest.metadata["has_applied_action_tape"] = bool(
                frame.applied_actions_recorded
            )
            for prefix, values in (
                ("before", frame.state_before),
                ("after", frame.state_after),
            ):
                for key in state_keys:
                    self.manifest.fields[f"{prefix}/{key}"] = _field_schema(
                        _copy_array(values[key])
                    )
            for key in action_keys:
                self.manifest.actions[key] = _field_schema(_copy_array(frame.actions[key]))
            for key in command_keys:
                self.manifest.commands[key] = _field_schema(_copy_array(frame.commands[key]))
            for key in noise_keys:
                self.manifest.noise[key] = _field_schema(_copy_array(frame.noise[key]))
            for key in applied_action_keys:
                self.manifest.applied_actions[key] = _field_schema(
                    _copy_array(frame.applied_actions[key])
                )
        elif (
            state_keys != self._state_keys
            or action_keys != self._action_keys
            or command_keys != self._command_keys
            or noise_keys != self._noise_keys
            or applied_action_keys != self._applied_action_keys
        ):
            raise ValueError("frame fields must remain fixed throughout an episode")
        elif bool(frame.applied_actions_recorded) != bool(
            self.manifest.metadata.get("has_applied_action_tape", False)
        ):
            raise ValueError("applied action tape presence changed within an episode")

        self._validate_frame_arrays(frame)
        self._buffer.append(frame)
        self._last_step = frame.step_index
        if len(self._buffer) >= self.manifest.segment_frames:
            self._flush_segment()

    def _validate_frame_arrays(self, frame: FrameRecord) -> None:
        assert self._state_keys is not None
        assert self._action_keys is not None
        assert self._command_keys is not None
        assert self._noise_keys is not None
        assert self._applied_action_keys is not None
        for prefix, values in (
            ("before", frame.state_before),
            ("after", frame.state_after),
        ):
            for key in self._state_keys:
                array = _copy_array(values[key])
                schema = self.manifest.fields[f"{prefix}/{key}"]
                if list(array.shape) != list(schema["shape"]) or str(array.dtype) != schema["dtype"]:
                    raise ValueError(f"field {prefix}/{key!r} shape or dtype changed")
        for key in self._action_keys:
            array = _copy_array(frame.actions[key])
            schema = self.manifest.actions[key]
            if list(array.shape) != list(schema["shape"]) or str(array.dtype) != schema["dtype"]:
                raise ValueError(f"action {key!r} shape or dtype changed")
        for label, keys, values, schemas in (
            ("command", self._command_keys, frame.commands, self.manifest.commands),
            ("noise", self._noise_keys, frame.noise, self.manifest.noise),
            (
                "applied_action",
                self._applied_action_keys,
                frame.applied_actions,
                self.manifest.applied_actions,
            ),
        ):
            for key in keys:
                array = _copy_array(values[key])
                schema = schemas[key]
                if list(array.shape) != list(schema["shape"]) or str(array.dtype) != schema["dtype"]:
                    raise ValueError(f"{label} {key!r} shape or dtype changed")

    def _flush_segment(self) -> None:
        if not self._buffer:
            return
        assert self._state_keys is not None
        assert self._action_keys is not None
        assert self._command_keys is not None
        assert self._noise_keys is not None
        assert self._applied_action_keys is not None
        arrays: dict[str, np.ndarray] = {
            "step_index": np.asarray([frame.step_index for frame in self._buffer], dtype=np.int64),
            "sim_time": np.asarray([frame.sim_time for frame in self._buffer], dtype=np.float64),
            "phase": np.asarray([frame.phase or "" for frame in self._buffer], dtype=np.str_),
            "events_json": np.asarray(
                [
                    json.dumps(list(frame.events), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    for frame in self._buffer
                ],
                dtype=np.str_,
            ),
        }
        for index, key in enumerate(self._state_keys):
            arrays[f"before_{index}"] = np.stack(
                [_copy_array(frame.state_before[key]) for frame in self._buffer]
            )
            arrays[f"after_{index}"] = np.stack(
                [_copy_array(frame.state_after[key]) for frame in self._buffer]
            )
        for index, key in enumerate(self._action_keys):
            arrays[f"action_{index}"] = np.stack(
                [_copy_array(frame.actions[key]) for frame in self._buffer]
            )
        for label, keys, attribute in (
            ("command", self._command_keys, "commands"),
            ("noise", self._noise_keys, "noise"),
            ("applied_action", self._applied_action_keys, "applied_actions"),
        ):
            for index, key in enumerate(keys):
                arrays[f"{label}_{index}"] = np.stack(
                    [_copy_array(getattr(frame, attribute)[key]) for frame in self._buffer]
                )

        segment_name = f"{len(self._segments):06d}.npz"
        final_path = self.segments_dir / segment_name
        temporary_path = self.segments_dir / f".{segment_name}.tmp"
        with temporary_path.open("wb") as stream:
            np.savez_compressed(stream, **arrays)
        checksum = _sha256(temporary_path)
        temporary_path.replace(final_path)
        self._segments.append(
            {
                "file": segment_name,
                "start_step": int(self._buffer[0].step_index),
                "end_step": int(self._buffer[-1].step_index),
                "frame_count": len(self._buffer),
                "sha256": checksum,
            }
        )
        self.manifest.metadata["segment_checksums"] = [
            segment["sha256"] for segment in self._segments
        ]
        self._buffer.clear()
        self._write_manifest()

    def _write_manifest(self) -> None:
        _atomic_json_write(self.root / "manifest.json", self.manifest.to_dict())

    def close(self) -> None:
        if self._closed:
            return
        self._flush_segment()
        self.manifest.status = "closed"
        self.manifest.metadata["frame_count"] = sum(
            int(segment["frame_count"]) for segment in self._segments
        )
        self.manifest.metadata["last_step"] = self._last_step
        self.manifest.metadata["segment_checksums"] = [
            segment["sha256"] for segment in self._segments
        ]
        _atomic_json_write(self.root / "manifest.json", self.manifest.to_dict())
        _atomic_json_write(
            self.root / "index.json",
            {"format_version": FORMAT_VERSION, "segments": self._segments},
        )
        self._closed = True

    def __enter__(self) -> "EpisodeWriter":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


class EpisodeReader:
    """Read and validate an episode written by :class:`EpisodeWriter`."""

    def __init__(self, root: str | Path, *, verify_checksums: bool = True):
        self.root = Path(root)
        try:
            manifest_payload = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
            index_payload = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"cannot read replay metadata from {self.root}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"replay metadata is not valid JSON: {exc}") from exc
        self.manifest = Manifest.from_dict(manifest_payload)
        if int(index_payload.get("format_version", 0)) != FORMAT_VERSION:
            raise ValueError("unsupported replay index format version")
        self.index = index_payload
        self.verify_checksums = bool(verify_checksums)
        self._segment_cache: dict[str, dict[str, np.ndarray]] = {}

    @property
    def state_keys(self) -> tuple[str, ...]:
        return tuple(self.manifest.metadata.get("state_keys", ()))

    @property
    def action_keys(self) -> tuple[str, ...]:
        return tuple(self.manifest.metadata.get("action_keys", ()))

    @property
    def command_keys(self) -> tuple[str, ...]:
        return tuple(self.manifest.metadata.get("command_keys", ()))

    @property
    def noise_keys(self) -> tuple[str, ...]:
        return tuple(self.manifest.metadata.get("noise_keys", ()))

    @property
    def applied_action_keys(self) -> tuple[str, ...]:
        return tuple(self.manifest.metadata.get("applied_action_keys", ()))

    def _load_segment(self, segment: dict[str, Any]) -> dict[str, np.ndarray]:
        filename = str(segment["file"])
        if filename in self._segment_cache:
            return self._segment_cache[filename]
        path = self.root / "segments" / filename
        if not path.is_file():
            raise ValueError(f"replay segment is missing: {path}")
        if self.verify_checksums and _sha256(path) != str(segment["sha256"]):
            raise ValueError(f"replay segment checksum mismatch: {path}")
        try:
            with np.load(path, allow_pickle=False) as loaded:
                arrays = {name: loaded[name].copy() for name in loaded.files}
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            raise ValueError(f"cannot load replay segment {path}: {exc}") from exc
        self._validate_segment_arrays(segment, arrays)
        self._segment_cache[filename] = arrays
        return arrays

    def _validate_segment_arrays(
        self, segment: dict[str, Any], arrays: dict[str, np.ndarray]
    ) -> None:
        required = {"step_index", "sim_time", "phase", "events_json"}
        missing = sorted(required - set(arrays))
        if missing:
            raise ValueError(
                f"replay segment {segment.get('file', '<unknown>')} is missing fields: {missing}"
            )
        frame_count = int(segment.get("frame_count", -1))
        steps = arrays["step_index"]
        if steps.ndim != 1 or len(steps) != frame_count:
            raise ValueError(f"replay segment {segment.get('file')} has an invalid step_index shape")
        if len(steps) == 0:
            raise ValueError(f"replay segment {segment.get('file')} is empty")
        if int(steps[0]) != int(segment["start_step"]) or int(steps[-1]) != int(segment["end_step"]):
            raise ValueError(f"replay segment {segment.get('file')} has inconsistent step bounds")
        for name in ("sim_time", "phase", "events_json"):
            if arrays[name].ndim != 1 or len(arrays[name]) != frame_count:
                raise ValueError(f"replay segment {segment.get('file')} has an invalid {name} shape")
        for index, key in enumerate(self.state_keys):
            for prefix in ("before", "after"):
                array_name = f"{prefix}_{index}"
                if array_name not in arrays:
                    raise ValueError(f"replay segment {segment.get('file')} is missing {array_name}")
                schema = self.manifest.fields.get(f"{prefix}/{key}")
                if schema is None:
                    raise ValueError(f"manifest is missing schema for {prefix}/{key}")
                expected_shape = (frame_count, *tuple(schema.get("shape", ())))
                if tuple(arrays[array_name].shape) != expected_shape:
                    raise ValueError(f"replay segment {segment.get('file')} has an invalid {array_name} shape")
                if str(arrays[array_name].dtype) != str(schema.get("dtype")):
                    raise ValueError(f"replay segment {segment.get('file')} has an invalid {array_name} dtype")
        for index, key in enumerate(self.action_keys):
            array_name = f"action_{index}"
            if array_name not in arrays:
                raise ValueError(f"replay segment {segment.get('file')} is missing {array_name}")
            schema = self.manifest.actions.get(key)
            if schema is None:
                raise ValueError(f"manifest is missing schema for action {key}")
            expected_shape = (frame_count, *tuple(schema.get("shape", ())))
            if tuple(arrays[array_name].shape) != expected_shape:
                raise ValueError(f"replay segment {segment.get('file')} has an invalid {array_name} shape")
            if str(arrays[array_name].dtype) != str(schema.get("dtype")):
                raise ValueError(f"replay segment {segment.get('file')} has an invalid {array_name} dtype")
        for label, keys, schemas in (
            ("command", self.command_keys, self.manifest.commands),
            ("noise", self.noise_keys, self.manifest.noise),
            (
                "applied_action",
                self.applied_action_keys,
                self.manifest.applied_actions,
            ),
        ):
            for index, key in enumerate(keys):
                array_name = f"{label}_{index}"
                if array_name not in arrays:
                    raise ValueError(f"replay segment {segment.get('file')} is missing {array_name}")
                schema = schemas.get(key)
                if schema is None:
                    raise ValueError(f"manifest is missing schema for {label} {key}")
                expected_shape = (frame_count, *tuple(schema.get("shape", ())))
                if tuple(arrays[array_name].shape) != expected_shape:
                    raise ValueError(f"replay segment {segment.get('file')} has an invalid {array_name} shape")
                if str(arrays[array_name].dtype) != str(schema.get("dtype")):
                    raise ValueError(f"replay segment {segment.get('file')} has an invalid {array_name} dtype")

    def validate_integrity(self) -> None:
        """Verify every indexed segment, checksum, shape, and manifest count."""

        segments = self.index.get("segments", ())
        if not isinstance(segments, list):
            raise ValueError("replay index segments must be a list")
        frame_count = 0
        for segment in segments:
            if not isinstance(segment, dict):
                raise ValueError("replay index contains a malformed segment entry")
            arrays = self._load_segment(segment)
            frame_count += len(arrays["step_index"])
        expected = self.manifest.metadata.get("frame_count")
        if expected is not None and int(expected) != frame_count:
            raise ValueError(
                f"replay manifest frame_count={expected} does not match segments={frame_count}"
            )

    def _find_row(self, step_index: int) -> tuple[dict[str, np.ndarray], int, bool]:
        step_index = int(step_index)
        terminal: tuple[dict[str, np.ndarray], int] | None = None
        for segment in self.index.get("segments", ()):
            start = int(segment["start_step"])
            end = int(segment["end_step"])
            if step_index < start or step_index > end + 1:
                continue
            arrays = self._load_segment(segment)
            steps = arrays["step_index"]
            exact = np.flatnonzero(steps == step_index)
            if exact.size:
                return arrays, int(exact[0]), False
            previous = np.flatnonzero(steps + 1 == step_index)
            if previous.size:
                terminal = (arrays, int(previous[0]))
        if terminal is not None:
            arrays, row = terminal
            return arrays, row, True
        raise ValueError(f"replay has no state for step {step_index}")

    def _frame_from_row(
        self, arrays: dict[str, np.ndarray], row: int, *, terminal: bool
    ) -> FrameRecord:
        state_before: dict[str, np.ndarray] = {}
        state_after: dict[str, np.ndarray] = {}
        for index, key in enumerate(self.state_keys):
            before = arrays[f"before_{index}"][row]
            after = arrays[f"after_{index}"][row]
            state_before[key] = after.copy() if terminal else before.copy()
            state_after[key] = after.copy()
            if terminal:
                state_after[key] = state_before[key].copy()
        actions = {
            key: arrays[f"action_{index}"][row].copy()
            for index, key in enumerate(self.action_keys)
        }
        commands = {
            key: arrays[f"command_{index}"][row].copy()
            for index, key in enumerate(self.command_keys)
        }
        noise = {
            key: arrays[f"noise_{index}"][row].copy()
            for index, key in enumerate(self.noise_keys)
        }
        applied_actions = {
            key: arrays[f"applied_action_{index}"][row].copy()
            for index, key in enumerate(self.applied_action_keys)
        }
        phase = str(arrays["phase"][row]) or None
        try:
            events = tuple(json.loads(str(arrays["events_json"][row])))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("replay segment contains invalid events JSON") from exc
        step = int(arrays["step_index"][row]) + (1 if terminal else 0)
        sim_time = float(arrays["sim_time"][row])
        if terminal:
            sim_time += float(self.manifest.environment.get("physics_dt", 0.0))
            actions = {}
            commands = {}
            noise = {}
            applied_actions = {}
            phase = None
            events = ()
        return FrameRecord(
            step,
            sim_time,
            state_before,
            state_after,
            actions,
            phase,
            events,
            commands=commands,
            noise=noise,
            applied_actions=applied_actions,
        )

    def read_frame(self, step_index: int) -> FrameRecord:
        arrays, row, terminal = self._find_row(step_index)
        return self._frame_from_row(arrays, row, terminal=terminal)

    def read_state(self, step_index: int) -> dict[str, np.ndarray]:
        return self.read_frame(step_index).state_before

    def summary(self) -> dict[str, Any]:
        self.validate_integrity()
        return {
            "format_version": self.manifest.format_version,
            "environment": self.manifest.environment,
            "target_count": len(self.manifest.targets),
            "segment_count": len(self.index.get("segments", ())),
            "frame_count": self.manifest.metadata.get("frame_count", 0),
            "replay_fidelity": self.manifest.replay_fidelity,
            "status": self.manifest.status,
        }
