"""Versioned Factory micro-test records for robot configuration admission."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = "eai.robot_configuration_microtest"
PROTOCOLS = {
    "factory_navigation_v1": {
        "axis": "scene_traversability",
        "required_fields": ("route_length_m", "sim_time_s", "final_error_m", "steps_taken"),
        "evidence_tier": "E6",
    },
    "factory_sensor_capture_v1": {
        "axis": "near_observation",
        "required_fields": ("frame_count", "valid_frame_count", "max_age_s"),
        "evidence_tier": "E6",
    },
    "factory_navigation_visit_v2": {
        "required_fields": ("sampled_prefix_path_m", "arrival_time_s", "arrival_error_m", "arrival_step"),
        "evidence_tier": "E6",
    },
    "factory_camera_capture_v2": {
        "required_fields": ("frame_count", "window_duration_s", "max_gap_s", "max_capture_position_error_m"),
        "evidence_tier": "E6",
        "max_gap_bound_s": 30.0,
    },
    "factory_inventory_detection_v2": {
        "required_fields": ("frame_count", "identified_frame_count",
                            "fused_localization_error_m", "observed_marker_id"),
        "evidence_tier": "E6",
    },
}


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _finite(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError("micro-test metrics must be finite numbers")
    result = float(value)
    if (positive and result <= 0) or (not positive and result < 0):
        raise ValueError("micro-test metric has an invalid sign")
    return result


def validate_microtest(record: Mapping[str, Any]) -> dict[str, Any]:
    if record.get("schema") != SCHEMA or record.get("version") not in {1, 2}:
        raise ValueError("unsupported configuration micro-test schema")
    protocol_id = record.get("protocol_id")
    protocol = PROTOCOLS.get(protocol_id)
    if protocol is None:
        raise ValueError(f"unknown micro-test protocol {protocol_id!r}")
    for field in ("configuration_id", "robot_key", "scenario_sha256", "report_sha256",
                  "condition_sha256", "attempted_at"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise ValueError(f"micro-test requires {field}")
    if any(len(record[field]) != 64 or any(character not in "0123456789abcdef" for character in record[field])
           for field in ("scenario_sha256", "report_sha256", "condition_sha256")):
        raise ValueError("micro-test hashes must be SHA-256")
    if not isinstance(record.get("condition"), Mapping) or record["condition_sha256"] != canonical_sha256(record["condition"]):
        raise ValueError("micro-test condition digest mismatch")
    if record.get("status") != "passed":
        raise ValueError("only a passed micro-test can enter a capability profile")
    if record.get("evidence_tier") != protocol["evidence_tier"]:
        raise ValueError("micro-test evidence tier does not match its protocol")
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        raise ValueError("micro-test metrics are required")
    for field in protocol["required_fields"]:
        if field not in metrics:
            raise ValueError(f"micro-test metric {field!r} is missing")
    for field, value in metrics.items():
        _finite(value, positive=field in {"route_length_m", "sim_time_s"})
    if protocol_id == "factory_navigation_v1":
        bound = record.get("acceptance", {}).get("final_error_bound_m", 0.5)
        _finite(bound, positive=True)
        if metrics["steps_taken"] < 1 or metrics["final_error_m"] > bound:
            raise ValueError("navigation micro-test did not meet its frozen acceptance bound")
    elif protocol_id == "factory_sensor_capture_v1" and (
            metrics["frame_count"] < 1 or metrics["valid_frame_count"] != metrics["frame_count"]):
        raise ValueError("sensor micro-test did not produce valid frames")
    elif protocol_id == "factory_navigation_visit_v2":
        bound = _finite(record.get("acceptance", {}).get("arrival_radius_m"), positive=True)
        if metrics["arrival_error_m"] > bound or metrics["arrival_step"] < 1:
            raise ValueError("navigation visit is outside its declared acceptance region")
    elif protocol_id == "factory_camera_capture_v2":
        position_bound = _finite(record.get("acceptance", {}).get("observer_position_bound_m"), positive=True)
        if (metrics["frame_count"] < 1 or metrics["window_duration_s"] <= 0
                or metrics["max_gap_s"] > protocol["max_gap_bound_s"]
                or metrics["max_capture_position_error_m"] > position_bound):
            raise ValueError("camera capture did not meet its frame, position or freshness bound")
    elif protocol_id == "factory_inventory_detection_v2":
        localization_bound = _finite(
            record.get("acceptance", {}).get("localization_error_bound_m"), positive=True)
        if (metrics["frame_count"] < 1 or metrics["identified_frame_count"] < 1
                or metrics["identified_frame_count"] > metrics["frame_count"]
                or metrics["fused_localization_error_m"] > localization_bound
                or metrics["observed_marker_id"] < 0):
            raise ValueError("inventory detection did not meet its identification or localization bound")
    if record["version"] == 2:
        if not protocol_id.endswith("_v2") or not isinstance(record.get("scope"), dict):
            raise ValueError("micro-test v2 requires a scoped v2 protocol")
        evidence = record.get("evidence")
        if not isinstance(evidence, dict) or any(not evidence.get(field) for field in (
                "scenario_path", "attempt_path", "attempt_sha256", "converter_sha256", "protocol_source_sha256")):
            raise ValueError("micro-test v2 requires source evidence")
        if not record.get("node_id") or not record.get("producer_id"):
            raise ValueError("micro-test v2 requires its node and producer")
    return dict(record)


def make_record(*, configuration_id: str, robot_key: str, protocol_id: str,
                scenario_sha256: str, report_sha256: str, condition: Mapping[str, Any],
                metrics: Mapping[str, Any], status: str = "passed",
                acceptance: Mapping[str, Any] | None = None,
                source_refs: Iterable[str] = ()) -> dict[str, Any]:
    protocol = PROTOCOLS.get(protocol_id)
    if protocol is None:
        raise ValueError(f"unknown micro-test protocol {protocol_id!r}")
    record = {
        "schema": SCHEMA, "version": 1, "configuration_id": configuration_id,
        "robot_key": robot_key, "protocol_id": protocol_id,
        "scenario_sha256": scenario_sha256, "report_sha256": report_sha256,
        "condition": dict(condition), "condition_sha256": canonical_sha256(condition),
        "metrics": dict(metrics), "status": status,
        "evidence_tier": protocol["evidence_tier"],
        "acceptance": dict(acceptance or {}), "source_refs": sorted(set(source_refs)),
        "limitations": [
            "configuration-specific Isaac evidence, not a manufacturer rating",
            "navigation and sensor capture do not establish grasp, payload, force, collision margin, wind or load dynamics",
        ],
        "attempted_at": datetime.now(timezone.utc).isoformat(),
    }
    return validate_microtest(record)


def aggregate_records(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    validated = [validate_microtest(record) for record in records]
    if not validated:
        raise ValueError("at least one micro-test record is required")
    identity = {(item["configuration_id"], item["protocol_id"], item["condition_sha256"])
                for item in validated}
    if len(identity) != 1:
        raise ValueError("micro-test aggregation mixes configuration, protocol, or conditions")
    if len({item["report_sha256"] for item in validated}) != len(validated):
        raise ValueError("duplicate report cannot count as a repeated micro-test")
    return {
        "schema": "eai.robot_configuration_microtest_summary", "version": 1,
        "configuration_id": validated[0]["configuration_id"],
        "robot_key": validated[0]["robot_key"], "protocol_id": validated[0]["protocol_id"],
        "condition_sha256": validated[0]["condition_sha256"], "sample_count": len(validated),
        "raw_metrics": {key: [item["metrics"][key] for item in validated]
                        for key in validated[0]["metrics"]},
        "pass_count": len(validated),
        "pass_rate": None,
        "denominator": "success records only; use the complete attempt ledger for failure rates",
        "statistical_interval": "not estimated from a single or non-independent layout",
        "evidence_ids": [item["report_sha256"] for item in validated],
        "limitations": validated[0]["limitations"],
    }


def measurement_for_profile(record: Mapping[str, Any]) -> dict[str, Any]:
    item = validate_microtest(record)
    if item["version"] != 2:
        raise ValueError("legacy micro-test lacks source evidence; regenerate it from an execution attempt")
    from tools.scenario_generation.record_configuration_microtests import verify_record_sources
    verify_record_sources(item)
    return {
        "kind": "protocol_check", "status": "passed",
        "configuration_id": item["configuration_id"], "protocol_id": item["protocol_id"], "sample_count": 1,
        "evidence_id": item["report_sha256"], "evidence_tier": item["evidence_tier"],
        "scope": dict(item["scope"]),
        "conditions": {"configuration_id": item["configuration_id"],
                        "condition_sha256": item["condition_sha256"],
                        "raw_metrics": dict(item["metrics"])},
    }


def load_records(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else data.get("records")
    if not isinstance(records, list):
        raise ValueError("micro-test input must be a record list or {records: [...]}")
    return [validate_microtest(record) for record in records]


__all__ = ["PROTOCOLS", "SCHEMA", "aggregate_records", "canonical_sha256",
           "load_records", "make_record", "measurement_for_profile", "validate_microtest"]
