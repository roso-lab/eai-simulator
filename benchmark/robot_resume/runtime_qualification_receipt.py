#!/usr/bin/env python3
"""Fail-closed CPU contract for per-robot runtime qualification evidence.

The module only validates evaluator-owned evidence.  It never starts Isaac,
CUDA, Kit, or a robot process, and it never upgrades a roster row by itself.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "heterocoord.robot_runtime_qualification_receipt.v1"
HISTORICAL_SCHEMA = "heterocoord.historical_runtime_qualification_receipt.v1"
CHECK_NAMES = ("load", "basic_function", "sensor_readback")
STATUS = frozenset({"qualified", "failed", "blocked_missing_evidence", "rejected"})
HEX = frozenset("0123456789abcdef")


class RuntimeReceiptError(ValueError):
    pass


def canonical_sha256(value: Any) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise RuntimeReceiptError("receipt is not JSON-stable") from exc
    return hashlib.sha256(payload).hexdigest()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeReceiptError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(ch not in HEX for ch in value):
        raise RuntimeReceiptError(f"{name} must be lowercase sha256")
    return value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise RuntimeReceiptError(f"{name} must be boolean")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeReceiptError(f"{name} must be an object")
    return value


def _check_row(value: Any, name: str) -> dict[str, Any]:
    row = dict(_mapping(value, name))
    required = {"passed", "evidence_digest", "details"}
    if set(row) != required:
        raise RuntimeReceiptError(f"{name} fields mismatch")
    _bool(row["passed"], f"{name}.passed")
    _digest(row["evidence_digest"], f"{name}.evidence_digest")
    _mapping(row["details"], f"{name}.details")
    return row


def _run_row(value: Any) -> dict[str, Any]:
    row = dict(_mapping(value, "run"))
    required = {"command", "command_digest", "source_revision", "environment_digest", "requested_device", "actual_device", "gpu_uuid", "seed", "serializer_version"}
    if set(row) != required:
        raise RuntimeReceiptError("run fields mismatch")
    _text(row["command"], "run.command")
    _digest(row["command_digest"], "run.command_digest")
    _text(row["source_revision"], "run.source_revision")
    _digest(row["environment_digest"], "run.environment_digest")
    _text(row["requested_device"], "run.requested_device")
    _text(row["actual_device"], "run.actual_device")
    _text(row["gpu_uuid"], "run.gpu_uuid")
    if type(row["seed"]) is not int:
        raise RuntimeReceiptError("run.seed must be an integer")
    _text(row["serializer_version"], "run.serializer_version")
    if canonical_sha256(row["command"]) != row["command_digest"]:
        raise RuntimeReceiptError("run.command_digest does not match command")
    if row["requested_device"] != row["actual_device"]:
        raise RuntimeReceiptError("requested and actual device differ")
    return row


def _cleanup_row(value: Any) -> dict[str, Any]:
    row = dict(_mapping(value, "cleanup"))
    required = {"telemetry_cleanup", "cleanup_digest", "df_before_bytes", "df_after_bytes", "free_before_bytes", "free_after_bytes"}
    if set(row) != required:
        raise RuntimeReceiptError("cleanup fields mismatch")
    _bool(row["telemetry_cleanup"], "cleanup.telemetry_cleanup")
    _digest(row["cleanup_digest"], "cleanup.cleanup_digest")
    for key in ("df_before_bytes", "df_after_bytes", "free_before_bytes", "free_after_bytes"):
        if type(row[key]) is not int or row[key] < 0:
            raise RuntimeReceiptError(f"cleanup.{key} must be a non-negative integer")
    return row


def _manipulation_row(value: Any) -> dict[str, Any]:
    row = dict(_mapping(value, "manipulation"))
    required = {"required", "pick", "place"}
    if set(row) != required or type(row["required"]) is not bool:
        raise RuntimeReceiptError("manipulation fields mismatch")
    for operation in ("pick", "place"):
        item = dict(_mapping(row[operation], f"manipulation.{operation}"))
        if set(item) != {"attempted", "succeeded", "evidence_digest"}:
            raise RuntimeReceiptError(f"manipulation.{operation} fields mismatch")
        _bool(item["attempted"], f"manipulation.{operation}.attempted")
        _bool(item["succeeded"], f"manipulation.{operation}.succeeded")
        _digest(item["evidence_digest"], f"manipulation.{operation}.evidence_digest")
        row[operation] = item
    if row["required"] is not True:
        raise RuntimeReceiptError("runtime receipt must declare manipulation requirement explicitly")
    return row


def _seal_payload(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in receipt.items() if key != "receipt_digest"}


def validate_receipt(receipt: Mapping[str, Any], *, roster_row: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise RuntimeReceiptError("receipt must be an object")
    if receipt.get("schema") == HISTORICAL_SCHEMA:
        try:
            from .historical_runtime_receipt import validate_historical_receipt
        except ImportError:  # direct-file invocation used by the local validator
            from historical_runtime_receipt import validate_historical_receipt
        try:
            return validate_historical_receipt(
                receipt, roster_row=roster_row,
                root=Path(__file__).resolve().parents[2])
        except ValueError as exc:
            raise RuntimeReceiptError(str(exc)) from exc
    row = dict(receipt)
    required = {"schema", "receipt_id", "instance_id", "source_resume_id", "profile_id", "admission_class", "platform_type", "configuration_role", "asset_digest", "capability_digest", "initial_state_digest", "run", "checks", "manipulation", "cleanup", "evaluator_digest", "status", "runtime_qualified", "failure", "formal_gates", "receipt_digest"}
    if set(row) != required:
        raise RuntimeReceiptError(f"receipt fields mismatch; missing={sorted(required-set(row))}, extra={sorted(set(row)-required)}")
    if row["schema"] != SCHEMA:
        raise RuntimeReceiptError("unsupported receipt schema")
    for key in ("receipt_id", "instance_id", "source_resume_id", "profile_id", "admission_class", "platform_type", "configuration_role"):
        _text(row[key], key)
    for key in ("asset_digest", "capability_digest", "initial_state_digest", "evaluator_digest", "receipt_digest"):
        _digest(row[key], key)
    if row["admission_class"] not in {"pool", "independent_arm_immediate"}:
        raise RuntimeReceiptError("unsupported admission_class")
    status = row["status"]
    if status not in STATUS:
        raise RuntimeReceiptError("unsupported receipt status")
    qualified = _bool(row["runtime_qualified"], "runtime_qualified")
    checks = _mapping(row["checks"], "checks")
    if set(checks) != set(CHECK_NAMES):
        raise RuntimeReceiptError("checks must contain load/basic_function/sensor_readback exactly")
    checked = {key: _check_row(checks[key], f"checks.{key}") for key in CHECK_NAMES}
    run = None if row["run"] is None else _run_row(row["run"])
    cleanup = None if row["cleanup"] is None else _cleanup_row(row["cleanup"])
    manipulation = None if row["manipulation"] is None else _manipulation_row(row["manipulation"])
    failure = None if row["failure"] is None else dict(_mapping(row["failure"], "failure"))
    if failure is not None:
        if set(failure) != {"code", "message", "evidence_path"}:
            raise RuntimeReceiptError("failure fields mismatch")
        _text(failure["code"], "failure.code")
        _text(failure["message"], "failure.message")
        if failure["evidence_path"] is not None:
            _text(failure["evidence_path"], "failure.evidence_path")
    gates = _mapping(row["formal_gates"], "formal_gates")
    if gates != {"physical_qualified": False, "rankable": False, "paper_score_eligible": False}:
        raise RuntimeReceiptError("runtime receipt cannot open formal gates")
    if roster_row is not None:
        if row["instance_id"] != roster_row.get("instance_id"):
            raise RuntimeReceiptError("instance_id does not match roster")
        expected = {
            "asset_digest": roster_row.get("asset_binding", {}).get("asset_digest"),
            "capability_digest": roster_row.get("capability_binding", {}).get("capability_digest"),
            "initial_state_digest": roster_row.get("initial_state_binding", {}).get("initial_state_digest"),
        }
        for key, value in expected.items():
            if row[key] != value:
                raise RuntimeReceiptError(f"{key} does not match roster")
        computed = {
            "asset_digest": canonical_sha256(roster_row.get("asset_binding", {}).get("asset_sources")),
            "capability_digest": canonical_sha256(roster_row.get("capability_binding", {}).get("source_input")),
            "initial_state_digest": canonical_sha256(roster_row.get("initial_state_binding", {}).get("initial_state")),
        }
        for key, value in computed.items():
            if value != expected[key]:
                raise RuntimeReceiptError(f"{key} does not match its bound payload")
    arm_required = row["configuration_role"] == "standalone_arm"
    if arm_required and status == "qualified":
        if manipulation is None:
            raise RuntimeReceiptError("standalone UR5/Z1 requires pick and place evidence")
        if not (manipulation["pick"]["attempted"] and manipulation["place"]["attempted"]):
            raise RuntimeReceiptError("standalone arm pick/place must both be attempted")
    if status == "qualified":
        if not qualified or run is None or cleanup is None or not cleanup["telemetry_cleanup"]:
            raise RuntimeReceiptError("qualified receipt lacks run/cleanup evidence")
        if not all(item["passed"] for item in checked.values()):
            raise RuntimeReceiptError("qualified receipt has a failed required check")
        if arm_required and not (manipulation["pick"]["succeeded"] and manipulation["place"]["succeeded"]):
            raise RuntimeReceiptError("qualified standalone arm lacks successful pick/place")
        if failure is not None:
            raise RuntimeReceiptError("qualified receipt cannot contain failure")
        if row["evaluator_digest"] == "0" * 64 or cleanup["cleanup_digest"] == "0" * 64:
            raise RuntimeReceiptError("qualified receipt requires non-empty evaluator and cleanup evidence")
        if any(item["evidence_digest"] == "0" * 64 for item in checked.values()):
            raise RuntimeReceiptError("qualified receipt requires evidence for every required check")
    else:
        if qualified:
            raise RuntimeReceiptError("non-qualified status cannot set runtime_qualified=true")
        if status == "failed" and failure is None:
            raise RuntimeReceiptError("failed receipt requires failure details")
    expected_seal = canonical_sha256(_seal_payload(row))
    if expected_seal != row["receipt_digest"]:
        raise RuntimeReceiptError("receipt_digest seal mismatch")
    row["checks"] = checked
    return row


def make_receipt(*, roster_row: Mapping[str, Any], status: str, receipt_id: str,
                 run: Mapping[str, Any] | None = None,
                 checks: Mapping[str, Mapping[str, Any]] | None = None,
                 manipulation: Mapping[str, Any] | None = None,
                 cleanup: Mapping[str, Any] | None = None,
                 evaluator_digest: str | None = None,
                 failure: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if status not in STATUS:
        raise RuntimeReceiptError("unsupported receipt status")
    checks = dict(checks or {name: {"passed": False, "evidence_digest": "0" * 64, "details": {}} for name in CHECK_NAMES})
    row = {
        "schema": SCHEMA, "receipt_id": _text(receipt_id, "receipt_id"),
        "instance_id": _text(roster_row["instance_id"], "instance_id"),
        "source_resume_id": _text(roster_row["source_resume_id"], "source_resume_id"),
        "profile_id": _text(roster_row["profile_id"], "profile_id"),
        "admission_class": roster_row["admission_class"],
        "platform_type": roster_row["platform_type"],
        "configuration_role": roster_row["configuration_role"],
        "asset_digest": roster_row["asset_binding"]["asset_digest"],
        "capability_digest": roster_row["capability_binding"]["capability_digest"],
        "initial_state_digest": roster_row["initial_state_binding"]["initial_state_digest"],
        "run": None if run is None else dict(run), "checks": checks,
        "manipulation": None if manipulation is None else dict(manipulation),
        "cleanup": None if cleanup is None else dict(cleanup),
        "evaluator_digest": evaluator_digest or "0" * 64,
        "status": status, "runtime_qualified": status == "qualified",
        "failure": None if failure is None else dict(failure),
        "formal_gates": {"physical_qualified": False, "rankable": False, "paper_score_eligible": False},
    }
    row["receipt_digest"] = canonical_sha256(row)
    return validate_receipt(row, roster_row=roster_row)


__all__ = ["CHECK_NAMES", "HISTORICAL_SCHEMA", "RuntimeReceiptError", "SCHEMA",
           "canonical_sha256", "make_receipt", "validate_receipt"]
