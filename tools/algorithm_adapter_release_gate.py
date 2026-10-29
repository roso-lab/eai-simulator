#!/usr/bin/env python3
"""Fail-closed audit for the six algorithm adapter release artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.validate_fixture_evidence_matrix import validate  # noqa: E402

EXPECTED = ["FMC-ATA", "BT-capabilities", "SADCHER", "HARL-HAPPO", "TeamWeaver-adapted-to-EAI", "EMOS"]


def run() -> dict:
    errors = []
    fixture_path = ROOT / "records/algorithm_adapters/FIXTURE_EVIDENCE_MATRIX_20261029.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    errors.extend(validate(fixture, ROOT))
    condition_path = ROOT / "records/algorithm_adapters/CONDITION_SUPPORT_MATRIX_20261029.json"
    conditions = json.loads(condition_path.read_text(encoding="utf-8"))
    if [row.get("algorithm_id") for row in conditions.get("algorithms", [])] != EXPECTED:
        errors.append("condition support matrix roster mismatch")
    if conditions.get("policy", "").find("pending") < 0:
        errors.append("condition support policy is missing pending fail-closed language")
    for row in conditions.get("algorithms", []):
        for name, item in row.get("conditions", {}).items():
            if item.get("status") not in {"supported", "unregistered", "not_applicable", "pending"}:
                errors.append(f"invalid condition status: {row.get('algorithm_id')}/{name}")
            if not item.get("reason"):
                errors.append(f"missing condition reason: {row.get('algorithm_id')}/{name}")
    if conditions["algorithms"][2]["conditions"]["P3-J"]["status"] != "pending":
        errors.append("SADCHER P3-J must remain pending")
    return {
        "schema": "heterocoord.algorithm_adapter_release_gate.v1",
        "date": "2026-10-29",
        "algorithm_roster": EXPECTED,
        "fixture_matrix": "valid" if not validate(fixture, ROOT) else "invalid",
        "condition_matrix": "valid" if not errors else "review_required",
        "cross_algorithm_comparison": "pending_common_condition_and_receipt",
        "errors": errors,
        "status": "pass_with_pending_boundaries" if not errors else "fail",
    }


def main() -> int:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass_with_pending_boundaries" else 1


if __name__ == "__main__":
    raise SystemExit(main())
