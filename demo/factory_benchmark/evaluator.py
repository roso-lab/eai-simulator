"""Evaluator-only contracts for maintained Factory benchmark fixtures."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _center(bounds: Mapping[str, Any]) -> tuple[float, float, float]:
    minimum = bounds.get("min")
    maximum = bounds.get("max")
    if not isinstance(minimum, list) or not isinstance(maximum, list):
        raise ValueError("world_bounds_m must contain min and max lists")
    if len(minimum) != 3 or len(maximum) != 3:
        raise ValueError("world_bounds_m coordinates must have length three")
    return tuple((float(low) + float(high)) / 2 for low, high in zip(minimum, maximum))


def load_fixture(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("inspection evaluator fixture must be an object")
    return payload


def validate_inspection_fixture(*, scenario_path: str | Path, usd_path: str | Path,
                                fixture: Mapping[str, Any], prim_audit: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a frozen F-INS truth/view fixture against current source files."""
    scenario_source = Path(scenario_path).resolve()
    usd_source = Path(usd_path).resolve()
    errors = []
    if fixture.get("schema") != "eai.factory_inspection_evaluator_fixture":
        errors.append("unsupported fixture schema")
    if fixture.get("version") != 1:
        errors.append("unsupported fixture version")
    if fixture.get("benchmark_task_id") != "F-INS-01":
        errors.append("fixture must target F-INS-01")
    hashes = fixture.get("source_hashes") or {}
    if hashes.get("scenario_sha256") != _sha256(scenario_source):
        errors.append("scenario hash mismatch")
    if hashes.get("usd_sha256") != _sha256(usd_source):
        errors.append("USD hash mismatch")
    bindings = {
        item.get("id"): item for item in prim_audit.get("bindings", [])
        if isinstance(item, Mapping)
    }
    target = fixture.get("target") or {}
    target_binding = bindings.get(target.get("binding_id"))
    if target_binding is None or not target_binding.get("valid"):
        errors.append("target binding is missing or invalid")
    else:
        if target_binding.get("prim_path") != target.get("prim_path"):
            errors.append("target prim path mismatch")
        if target_binding.get("role") != "inspection_target_visual_reference":
            errors.append("target binding is not a visual reference")
        try:
            distance = math.dist(_center(target_binding["world_bounds_m"]),
                                 tuple(float(value) for value in target["ground_truth_position_m"]))
            if distance > float(target.get("position_tolerance_m", 0.01)):
                errors.append("target ground truth position mismatch")
        except (KeyError, TypeError, ValueError):
            errors.append("target ground truth position is malformed")
    try:
        scenario = json.loads(scenario_source.read_text(encoding="utf-8"))
        nodes = {node["id"]: node for node in scenario["phase_graph"]["nodes"]}
        target_ids = {item["id"] for item in scenario["composition"]["targets"]}
        subject_ref = target["subject_ref"]
        if subject_ref not in target_ids:
            errors.append("target subject_ref is not a scenario target")
        views = fixture.get("views")
        if not isinstance(views, list) or len(views) < 2:
            errors.append("at least two evaluator views are required")
        else:
            view_ids = [view.get("id") for view in views]
            if len(view_ids) != len(set(view_ids)):
                errors.append("duplicate evaluator view id")
            for view in views:
                node = nodes.get(view.get("node_id"))
                if node is None:
                    errors.append(f"unknown view node {view.get('node_id')!r}")
                    continue
                observed_ref = node.get("observation_ref") or node.get("target_ref")
                if observed_ref != subject_ref:
                    errors.append(f"{view.get('node_id')}: observation subject mismatch")
                if not view.get("frame_id"):
                    errors.append(f"{view.get('node_id')}: frame_id is required")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        errors.append(f"scenario structure is malformed: {exc}")
    return {
        "schema": "eai.factory_inspection_evaluator_validation",
        "validated": not errors,
        "errors": errors,
        "ground_truth_source": "evaluator_only",
        "does_not_validate": [
            "detector accuracy without detector outputs",
            "occlusion robustness",
            "physical manipulation or E7 evidence",
        ],
    }


__all__ = ["load_fixture", "validate_inspection_fixture"]
