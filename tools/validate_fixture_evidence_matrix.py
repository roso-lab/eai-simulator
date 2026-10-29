#!/usr/bin/env python3
"""Validate adapter fixture evidence without promoting benchmark status."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "records/algorithm_adapters/FIXTURE_EVIDENCE_MATRIX_20261029.json"
EXPECTED = (
    "FMC-ATA",
    "BT-capabilities",
    "SADCHER",
    "HARL-HAPPO",
    "TeamWeaver-adapted-to-EAI",
    "EMOS",
)
FIXTURES = ("native_decision_only", "input_rejection", "execution_evidence_boundary")
STATUSES = {"verified", "pending"}


def validate(data: dict[str, Any], root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    if data.get("schema") != "heterocoord.algorithm_fixture_evidence.v1":
        errors.append("unexpected schema")
    if data.get("comparison_status") != "pending_common_condition_and_receipt":
        errors.append("comparison status must remain pending")
    algorithms = data.get("algorithms")
    if not isinstance(algorithms, dict) or tuple(algorithms) != EXPECTED:
        errors.append("algorithm roster/order does not match the six primary columns")
        return errors
    for algorithm_id, record in algorithms.items():
        if not isinstance(record, dict) or set(record) != set(FIXTURES):
            errors.append(f"{algorithm_id}: fixture keys are incomplete or unexpected")
            continue
        for fixture in FIXTURES:
            item = record[fixture]
            if not isinstance(item, dict) or item.get("status") not in STATUSES:
                errors.append(f"{algorithm_id}/{fixture}: invalid status record")
                continue
            status = item["status"]
            receipt = item.get("receipt")
            reason = item.get("reason")
            result = item.get("result")
            if status == "verified":
                if not isinstance(receipt, str) or not receipt:
                    errors.append(f"{algorithm_id}/{fixture}: verified entry needs receipt")
                elif not (root / receipt).is_file():
                    errors.append(f"{algorithm_id}/{fixture}: receipt does not exist: {receipt}")
                else:
                    path = root / receipt
                    if path.stat().st_size == 0:
                        errors.append(f"{algorithm_id}/{fixture}: receipt is empty: {receipt}")
                    elif path.suffix == ".json":
                        try:
                            parsed = json.loads(path.read_text(encoding="utf-8"))
                            if not isinstance(parsed, (dict, list)):
                                errors.append(f"{algorithm_id}/{fixture}: JSON receipt is not object/list")
                        except (OSError, json.JSONDecodeError) as exc:
                            errors.append(f"{algorithm_id}/{fixture}: invalid JSON receipt: {exc}")
                    elif path.suffix == ".xml":
                        try:
                            ET.parse(path)
                        except (OSError, ET.ParseError) as exc:
                            errors.append(f"{algorithm_id}/{fixture}: invalid XML receipt: {exc}")
            if status == "pending":
                if receipt is not None:
                    errors.append(f"{algorithm_id}/{fixture}: pending entry cannot have receipt")
                if not isinstance(reason, str) or not reason.strip():
                    errors.append(f"{algorithm_id}/{fixture}: pending entry needs reason")
            if status == "verified" and result is not None and not isinstance(result, str):
                errors.append(f"{algorithm_id}/{fixture}: result must be text")
    sadcher = algorithms["SADCHER"]["execution_evidence_boundary"]
    if sadcher.get("status") != "pending":
        errors.append("SADCHER joint execution boundary must remain pending")
    return errors


def main() -> int:
    data = json.loads(MATRIX.read_text(encoding="utf-8"))
    errors = validate(data)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("fixture evidence matrix: valid; no pending item promoted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
