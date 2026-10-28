#!/usr/bin/env python3
"""CPU-only queue runner for pre-collected robot qualification receipts.

This runner consumes evaluator-produced JSON receipts.  It never launches a
simulator and never manufactures a passing receipt.  Missing or malformed
evidence produces a sealed non-qualified receipt which remains auditable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

try:
    from .runtime_qualification_receipt import RuntimeReceiptError, make_receipt, validate_receipt
except ImportError:  # direct `python benchmark/robot_resume/...py` convenience
    from runtime_qualification_receipt import RuntimeReceiptError, make_receipt, validate_receipt


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _roster(path: Path) -> list[dict[str, Any]]:
    data = _load(path)
    if data.get("schema_id") != "heterocoord.robot_runtime_roster" or len(data.get("robots", [])) != 27:
        raise ValueError("runtime roster must be the v1.0 27-entry roster")
    rows = list(data["robots"])
    if len({row["instance_id"] for row in rows}) != len(rows):
        raise ValueError("runtime roster contains duplicate instance IDs")
    return rows


def _blocked(row: Mapping[str, Any], *, code: str, message: str, evidence_path: str | None, receipt_id: str) -> dict[str, Any]:
    return make_receipt(
        roster_row=row, status="blocked_missing_evidence" if code == "missing_evidence" else "rejected",
        receipt_id=receipt_id,
        failure={"code": code, "message": message, "evidence_path": evidence_path},
    )


def run_queue(*, roster_path: str | Path, evidence_dir: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Validate one receipt per roster entry in deterministic order.

    ``evidence_dir/<instance_id>.json`` is an evaluator-owned input.  Every
    roster row gets an output receipt, including rows with no input evidence.
    Existing failed evidence is copied semantically (same sealed JSON) rather
    than replaced by a pass or silently retried.
    """
    rows = _roster(Path(roster_path))
    evidence_root = Path(evidence_dir)
    output_root = Path(output_dir)
    receipt_root = output_root / "receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)
    report_rows = []
    for row in rows:
        instance_id = row["instance_id"]
        source = evidence_root / f"{instance_id}.json"
        output_path = receipt_root / f"{instance_id}.json"
        receipt = None
        if not source.is_file():
            receipt = _blocked(row, code="missing_evidence", message="evaluator receipt is absent; runtime qualification remains false", evidence_path=str(source), receipt_id=f"{instance_id}.missing")
        else:
            try:
                candidate = _load(source)
                receipt = validate_receipt(candidate, roster_row=row)
            except (OSError, ValueError, RuntimeReceiptError, json.JSONDecodeError) as exc:
                receipt = _blocked(row, code="invalid_evidence", message=str(exc), evidence_path=str(source), receipt_id=f"{instance_id}.rejected")
        output_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_rows.append({"instance_id": instance_id, "source": str(source), "output": str(output_path), "status": receipt["status"], "runtime_qualified": receipt["runtime_qualified"], "failure_code": (receipt.get("failure") or {}).get("code")})
    summary = {
        "schema": "heterocoord.robot_runtime_qualification_batch.v1",
        "scope": "cpu_receipt_validation_only",
        "simulator_started": False,
        "gpu_started": False,
        "formal_gates": {"physical_qualified": False, "rankable": False, "paper_score_eligible": False},
        "total": len(report_rows),
        "qualified": sum(item["runtime_qualified"] for item in report_rows),
        "non_qualified": sum(not item["runtime_qualified"] for item in report_rows),
        "rows": report_rows,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "batch_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def promote_roster(*, roster_path: str | Path, receipt_dir: str | Path,
                   output_path: str | Path) -> dict[str, Any]:
    """Embed evaluator receipts into a new roster revision.

    Promotion is explicit and fail-closed: a row becomes qualified only when
    its exact instance receipt validates against all three roster digests and
    has ``status=qualified``.  Missing, duplicate, failed, or mismatched
    receipts remain unqualified; failed receipts may still be embedded for
    audit.  The input roster is never modified in place.
    """
    roster_path = Path(roster_path)
    receipt_dir = Path(receipt_dir)
    output_path = Path(output_path)
    roster = _load(roster_path)
    if roster.get("schema_id") != "heterocoord.robot_runtime_roster" or len(roster.get("robots", [])) != 27:
        raise ValueError("runtime roster must be the v1.0 27-entry roster")
    promoted = json.loads(json.dumps(roster, ensure_ascii=False))
    by_id = {row["instance_id"]: row for row in promoted["robots"]}
    if len(by_id) != 27:
        raise ValueError("runtime roster contains duplicate instance IDs")
    rows = []
    seen_receipt_ids: set[str] = set()
    for instance_id, row in by_id.items():
        candidates = sorted(receipt_dir.glob(f"{instance_id}*.json")) if receipt_dir.is_dir() else []
        if len(candidates) > 1:
            rows.append({"instance_id": instance_id, "status": "rejected", "runtime_qualified": False, "reason": "duplicate receipt candidates"})
            continue
        if not candidates:
            rows.append({"instance_id": instance_id, "status": "missing", "runtime_qualified": False, "reason": "receipt missing"})
            continue
        try:
            candidate = _load(candidates[0])
            receipt_id = candidate.get("receipt_id") if isinstance(candidate, Mapping) else None
            if receipt_id in seen_receipt_ids:
                raise RuntimeReceiptError("duplicate receipt_id")
            seen_receipt_ids.add(receipt_id)
            receipt = validate_receipt(candidate, roster_row=row)
        except (OSError, ValueError, RuntimeReceiptError, json.JSONDecodeError) as exc:
            rows.append({"instance_id": instance_id, "status": "rejected", "runtime_qualified": False, "reason": str(exc)})
            continue
        row["runtime_qualification_receipt"] = receipt
        row["runtime_qualified"] = receipt["status"] == "qualified"
        rows.append({"instance_id": instance_id, "status": receipt["status"], "runtime_qualified": row["runtime_qualified"], "reason": None})
    promoted["schema_version"] = "1.0.0"
    promoted["promotion"] = {"schema": "heterocoord.robot_runtime_roster_promotion.v1", "source_roster": str(roster_path), "receipt_dir": str(receipt_dir), "qualified_count": sum(item["runtime_qualified"] for item in rows), "rows": rows}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(promoted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return promoted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--promote-receipts", type=Path, help="Embed receipts from this directory into a new roster")
    parser.add_argument("--promoted-roster", type=Path, help="Output path for --promote-receipts")
    args = parser.parse_args(argv)
    if args.promote_receipts is not None:
        if args.promoted_roster is None:
            parser.error("--promoted-roster is required with --promote-receipts")
        promoted = promote_roster(roster_path=args.roster, receipt_dir=args.promote_receipts, output_path=args.promoted_roster)
        print(json.dumps({"scope": "cpu_receipt_promotion_only", "qualified": sum(bool(row["runtime_qualified"]) for row in promoted["robots"]), "total": len(promoted["robots"]), "simulator_started": False, "gpu_started": False}, ensure_ascii=False))
        return 0
    summary = run_queue(roster_path=args.roster, evidence_dir=args.evidence_dir, output_dir=args.output_dir)
    print(json.dumps({key: summary[key] for key in ("schema", "scope", "simulator_started", "gpu_started", "total", "qualified", "non_qualified")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
