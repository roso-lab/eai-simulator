"""Scene-owned benchmark manifest construction and validation."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from demo.benchmark.protocol import (
    MODERN_HOUSE_SCENE_KEY,
    file_sha256, _certificate_view, _certificate_prerequisites,
    protocol_scope_metadata, validate_protocol_scope,
)


def build_modern_house_manifest(
    instance_paths: Iterable[str | Path],
    *,
    payload_policy: Mapping[str, float],
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Build a static Modern House manifest from existing scenario inputs."""
    from EAI.scenario_spec.benchmark_contract import benchmark_eligibility_contract

    base = Path(root).resolve() if root is not None else None
    paths = [Path(path) for path in instance_paths]
    if not paths:
        raise ValueError("at least one Modern House scenario is required")
    entries = []
    for path in sorted(paths, key=lambda item: item.name):
        resolved = (base / path).resolve() if base is not None and not path.is_absolute() else path.resolve()
        data = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("scene_key") != MODERN_HOUSE_SCENE_KEY:
            raise ValueError(f"Modern House manifest requires scene_key={MODERN_HOUSE_SCENE_KEY!r}: {resolved}")
        contract = benchmark_eligibility_contract(data, payload_policy=dict(payload_policy))
        entries.append({
            "id": data.get("task_name", resolved.stem),
            "file": str(resolved.relative_to(base)) if base is not None else str(resolved),
            "scene_key": MODERN_HOUSE_SCENE_KEY,
            "scenario_sha256": file_sha256(resolved),
            "certificate": _certificate_view(contract),
            "certificate_prerequisites": _certificate_prerequisites(data),
        })
    return {
        "schema": 1,
        "benchmark_id": "modern_house_r1",
        "scope": protocol_scope_metadata("modern_house"),
        "scene_key": MODERN_HOUSE_SCENE_KEY,
        "payload_policy": dict(payload_policy),
        "instances": entries,
        "metadata": deepcopy(MODERN_HOUSE_R1_METADATA),
    }


def validate_modern_house_manifest(
    manifest: Mapping[str, Any], *, root: str | Path | None = None
) -> None:
    """Validate Modern House source hashes and symbolic certificates."""
    from EAI.scenario_spec.benchmark_contract import benchmark_eligibility_contract

    if manifest.get("schema") != 1 or manifest.get("benchmark_id") != "modern_house_r1":
        raise ValueError("unsupported Modern House benchmark manifest")
    if manifest.get("scene_key") != MODERN_HOUSE_SCENE_KEY:
        raise ValueError("Modern House manifest has the wrong scene_key")
    validate_protocol_scope([], scope="modern_house", pool_scene_key=manifest.get("scene_key"))
    policy = manifest.get("payload_policy")
    instances = manifest.get("instances")
    if not isinstance(policy, dict) or not policy or not isinstance(instances, list) or not instances:
        raise ValueError("Modern House manifest requires payload_policy and instances")
    base = Path(root).resolve() if root is not None else None
    for entry in instances:
        if not isinstance(entry, dict) or entry.get("scene_key") != MODERN_HOUSE_SCENE_KEY:
            raise ValueError("Modern House manifest instance has the wrong scene_key")
        raw_file = entry.get("file")
        if not isinstance(raw_file, str):
            raise ValueError("Modern House manifest instance has no file")
        path = Path(raw_file)
        resolved = (base / path).resolve() if base is not None and not path.is_absolute() else path.resolve()
        data = json.loads(resolved.read_text(encoding="utf-8"))
        if data.get("scene_key") != MODERN_HOUSE_SCENE_KEY:
            raise ValueError(f"Modern House manifest points to another scene: {resolved}")
        if entry.get("scenario_sha256") != file_sha256(resolved):
            raise ValueError(f"scenario hash mismatch: {resolved}")
        contract = benchmark_eligibility_contract(data, payload_policy=policy)
        if entry.get("certificate") != _certificate_view(contract):
            raise ValueError(f"certificate mismatch: {resolved}")
        if entry.get("certificate_prerequisites") != _certificate_prerequisites(data):
            raise ValueError(f"certificate prerequisites mismatch: {resolved}")
    if any(key in manifest for key in ("settle_reports", "execution_reports")):
        raise ValueError("Modern House manifest must not contain settle/execution reports")
