"""Scene-owned benchmark manifest construction and validation."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from demo.benchmark.protocol import (
    WAREHOUSE_SCENE_KEY, WAREHOUSE_R1_ID, WAREHOUSE_R1_METADATA,
    file_sha256, _certificate_view, _certificate_prerequisites,
    protocol_scope_metadata, validate_protocol_scope,
)


def _load_warehouse_scenario(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read scenario JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("scene_key") != WAREHOUSE_SCENE_KEY:
        raise ValueError(f"Warehouse manifest requires scene_key={WAREHOUSE_SCENE_KEY!r}: {path}")
    return data


def build_warehouse_manifest(
    instance_paths: Iterable[str | Path],
    *,
    payload_policy: Mapping[str, float],
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Build a pure hash/certificate manifest from existing scenario inputs.

    Eligibility failures are retained as honest admission results.  No settle
    label or execution report is added.
    """
    from EAI.scenario_spec.benchmark_contract import benchmark_eligibility_contract

    base = Path(root).resolve() if root is not None else None
    paths = [Path(path) for path in instance_paths]
    if not paths:
        raise ValueError("at least one Warehouse scenario is required")
    entries = []
    for path in sorted(paths, key=lambda item: item.name):
        resolved = (base / path).resolve() if base is not None and not path.is_absolute() else path.resolve()
        data = _load_warehouse_scenario(resolved)
        contract = benchmark_eligibility_contract(data, payload_policy=dict(payload_policy))
        entries.append({
            "id": data.get("task_name", resolved.stem),
            "file": str(resolved.relative_to(base)) if base is not None else str(resolved),
            "scene_key": data["scene_key"],
            "scenario_sha256": file_sha256(resolved),
            "certificate": _certificate_view(contract),
            "certificate_prerequisites": _certificate_prerequisites(data),
        })
    return {
        "schema": 1,
        "benchmark_id": WAREHOUSE_R1_ID,
        "scope": protocol_scope_metadata("warehouse"),
        "scene_key": WAREHOUSE_SCENE_KEY,
        "payload_policy": dict(payload_policy),
        "instances": entries,
        "metadata": deepcopy(WAREHOUSE_R1_METADATA),
    }


def validate_warehouse_manifest(
    manifest: Mapping[str, Any], *, root: str | Path | None = None
) -> None:
    """Validate source hashes, scene, policy, and certificate prerequisites."""
    from EAI.scenario_spec.benchmark_contract import benchmark_eligibility_contract

    if manifest.get("schema") != 1 or manifest.get("benchmark_id") != WAREHOUSE_R1_ID:
        raise ValueError("unsupported Warehouse benchmark manifest")
    if manifest.get("scene_key") != WAREHOUSE_SCENE_KEY:
        raise ValueError("Warehouse manifest has the wrong scene_key")
    validate_protocol_scope([], scope="warehouse", pool_scene_key=manifest.get("scene_key"))
    policy = manifest.get("payload_policy")
    if not isinstance(policy, dict) or not policy:
        raise ValueError("Warehouse manifest requires a non-empty payload_policy")
    instances = manifest.get("instances")
    if not isinstance(instances, list) or not instances:
        raise ValueError("Warehouse manifest requires instances")
    base = Path(root).resolve() if root is not None else None
    for entry in instances:
        if not isinstance(entry, dict) or entry.get("scene_key") != WAREHOUSE_SCENE_KEY:
            raise ValueError("Warehouse manifest instance has the wrong scene_key")
        raw_file = entry.get("file")
        if not isinstance(raw_file, str):
            raise ValueError("Warehouse manifest instance has no file")
        path = Path(raw_file)
        resolved = (base / path).resolve() if base is not None and not path.is_absolute() else path.resolve()
        data = _load_warehouse_scenario(resolved)
        if entry.get("scenario_sha256") != file_sha256(resolved):
            raise ValueError(f"scenario hash mismatch: {resolved}")
        contract = benchmark_eligibility_contract(data, payload_policy=policy)
        if entry.get("certificate") != _certificate_view(contract):
            raise ValueError(f"certificate mismatch: {resolved}")
        if entry.get("certificate_prerequisites") != _certificate_prerequisites(data):
            raise ValueError(f"certificate prerequisites mismatch: {resolved}")
    if any(key in manifest for key in ("reports", "settle_reports", "execution_reports")):
        raise ValueError("Warehouse manifest must not contain settle/execution reports")
