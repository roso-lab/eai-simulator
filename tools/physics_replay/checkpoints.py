"""Atomic JSON/NPZ storage for versioned component checkpoints."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping
import zipfile

import numpy as np

from .components import ComponentSnapshot


CHECKPOINT_FORMAT_VERSION = 1


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_numpy(value: Any) -> np.ndarray | None:
    if isinstance(value, np.ndarray):
        return value.copy()
    if hasattr(value, "detach") and hasattr(value, "cpu"):
        return np.asarray(value.detach().cpu()).copy()
    return None


def _encode_tree(value: Any, arrays: dict[str, np.ndarray]) -> Any:
    array = _as_numpy(value)
    if array is not None:
        if array.dtype == object:
            raise TypeError("unsupported object-dtype array in checkpoint state")
        key = f"array_{len(arrays):06d}"
        arrays[key] = array
        return {"__array__": key}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        encoded = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("checkpoint mapping keys must be strings")
            encoded[key] = _encode_tree(item, arrays)
        return encoded
    if isinstance(value, tuple):
        return {"__tuple__": [_encode_tree(item, arrays) for item in value]}
    if isinstance(value, list):
        return [_encode_tree(item, arrays) for item in value]
    raise TypeError(f"unsupported checkpoint state value: {type(value).__name__}")


def _decode_tree(value: Any, arrays: Mapping[str, np.ndarray]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"__array__"}:
            key = str(value["__array__"])
            if key not in arrays:
                raise ValueError(f"checkpoint array reference is missing: {key}")
            return arrays[key].copy()
        if set(value) == {"__tuple__"}:
            return tuple(_decode_tree(item, arrays) for item in value["__tuple__"])
        return {key: _decode_tree(item, arrays) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_tree(item, arrays) for item in value]
    return value


class CheckpointStore:
    """Persist component snapshots under one replay episode directory."""

    def __init__(self, episode_dir: str | Path):
        self.episode_dir = Path(episode_dir)
        self.root = self.episode_dir / "checkpoints"
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, step_index: int) -> tuple[Path, Path]:
        stem = f"{int(step_index):08d}"
        return self.root / f"{stem}.json", self.root / f"{stem}.npz"

    def write(
        self, step_index: int, snapshots: Mapping[str, ComponentSnapshot]
    ) -> Path:
        step_index = int(step_index)
        if step_index < 0:
            raise ValueError("checkpoint step_index must be non-negative")
        json_path, array_path = self._paths(step_index)
        if json_path.exists() or array_path.exists():
            raise FileExistsError(f"checkpoint already exists at step {step_index}")
        arrays: dict[str, np.ndarray] = {}
        components = {}
        for name in sorted(snapshots):
            snapshot = snapshots[name]
            if snapshot.name != name:
                raise ValueError(f"component snapshot name mismatch for {name}")
            components[name] = {
                "version": int(snapshot.version),
                "state": _encode_tree(snapshot.state, arrays),
            }

        array_temp = array_path.with_name(f".{array_path.name}.tmp")
        with array_temp.open("wb") as stream:
            np.savez_compressed(stream, **arrays)
        checksum = _sha256(array_temp)
        array_temp.replace(array_path)

        payload = {
            "format_version": CHECKPOINT_FORMAT_VERSION,
            "step_index": step_index,
            "array_file": array_path.name,
            "array_sha256": checksum,
            "components": components,
        }
        json_temp = json_path.with_name(f".{json_path.name}.tmp")
        json_temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        json_temp.replace(json_path)
        return json_path

    def read(self, step_index: int) -> dict[str, ComponentSnapshot]:
        json_path, expected_array_path = self._paths(step_index)
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"cannot read checkpoint metadata: {json_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid checkpoint JSON: {json_path}") from exc
        if int(payload.get("format_version", 0)) != CHECKPOINT_FORMAT_VERSION:
            raise ValueError("unsupported checkpoint format version")
        if int(payload.get("step_index", -1)) != int(step_index):
            raise ValueError("checkpoint step_index mismatch")
        array_path = self.root / str(payload.get("array_file", ""))
        if array_path != expected_array_path or not array_path.is_file():
            raise ValueError("checkpoint array file is missing or mismatched")
        if _sha256(array_path) != str(payload.get("array_sha256", "")):
            raise ValueError("checkpoint array checksum mismatch")
        try:
            with np.load(array_path, allow_pickle=False) as loaded:
                arrays = {name: loaded[name].copy() for name in loaded.files}
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise ValueError(f"cannot load checkpoint arrays: {array_path}") from exc

        components = payload.get("components")
        if not isinstance(components, dict):
            raise ValueError("checkpoint components must be an object")
        return {
            name: ComponentSnapshot(
                name,
                int(item["version"]),
                _decode_tree(item["state"], arrays),
            )
            for name, item in components.items()
        }

    def list_steps(self) -> tuple[int, ...]:
        return tuple(
            sorted(int(path.stem) for path in self.root.glob("*.json") if path.stem.isdigit())
        )
