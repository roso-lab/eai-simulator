"""Run a registered original algorithm on an EAI task package.

This decision-only CLI does not instantiate Isaac or claim mission success.
An EAI host uses build_native_adapter() with its own executor and evaluator.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import traceback

from benchmark.native_algorithms import PAPER_ALGORITHMS, build_native_adapter
from algorithm.system.coordination.native_runtime import sha256_file
from task.contracts import EaiTaskPackage, TaskRunRequest
from task.runner import TaskRunner


def write_new_report(path: Path, report: dict) -> None:
    """Publish a complete receipt without replacing any existing artifact."""
    data = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".native-receipt-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Same-filesystem hard link is atomic and refuses concurrent winners.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", required=True, choices=PAPER_ALGORITHMS)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--config", type=Path, help="JSON kwargs for the native adapter; no secrets")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve() in {args.task.resolve(), args.config.resolve() if args.config else None}:
        parser.error("report cannot overwrite an input task or configuration")
    if os.path.lexists(args.report):
        parser.error("report already exists; use a new path to preserve evidence and model artifacts")
    started = time.monotonic()
    report = {"schema": "heterocoord.native_adapter_run.v1", "algorithm": args.algorithm,
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "evidence_scope": "native_decision_only", "physical_success": None,
              "simulation_acceptance_passed": False}
    code = 1
    adapter = None
    input_hashes = {}
    try:
        report["task_sha256"] = sha256_file(args.task)
        report["config_sha256"] = sha256_file(args.config) if args.config else None
        input_hashes = {args.task: report["task_sha256"]}
        if args.config:
            input_hashes[args.config] = report["config_sha256"]
        config = json.loads(args.config.read_text()) if args.config else {}
        task = EaiTaskPackage.from_json(args.task)
        if args.algorithm == "emos" and config.get("schema") == "heterocoord.emos_native_config.v1":
            from tools.algorithm_adapters.emos_config import load_config
            config = load_config(args.config)
            adapter = build_native_adapter("emos", source_root=args.source_root, json_config=config,
                                           config_base_dir=args.config.resolve().parent)
        else:
            adapter = build_native_adapter(args.algorithm, source_root=args.source_root, **config)
        result = TaskRunner().run(TaskRunRequest(task), adapter)
        report.update(status="native_decision_returned", result=result.to_dict(),
                      provenance=dict(result.decision.provenance))
        code = 0
    except Exception as exc:
        # No substituted decision and no successful receipt on an error.
        status = "blocked" if type(exc).__name__ == "NativeEmosUnavailable" else "not_passed"
        report.update(status=status, error_type=type(exc).__name__, error=str(exc))
        na_reason = getattr(exc, "na_reason", None)
        if na_reason is not None:
            report["N/A_reason"] = str(na_reason)
            report["evidence_scope"] = "native_decision_scope_rejected"
        # Preserve rejected native outputs (e.g. coalitions) without inventing
        # an executable single-robot assignment.
        rejected = getattr(exc, "decision", None)
        if rejected is not None:
            report["rejected_native_decision"] = {
                "assignments": [vars(item) for item in rejected.assignments],
                "unassigned_tasks": list(rejected.unassigned_tasks),
                "provenance": dict(rejected.provenance),
            }
        native_result = getattr(exc, "native_result", None)
        if native_result is not None:
            report["rejected_native_result"] = native_result
        native_subtasks = getattr(exc, "native_subtasks", None)
        if native_subtasks is not None:
            report["rejected_native_subtasks"] = native_subtasks
        for field in ("model_requests", "endpoint_receipt", "native_source_provenance"):
            value = getattr(exc, field, None)
            if value is not None:
                report[field] = value
        traceback.print_exc()
    finally:
        close = getattr(adapter, "close", None)
        if close is not None:
            try:
                close()
            except Exception as exc:
                report.update(status="not_passed", cleanup_error=type(exc).__name__)
                code = 1
        report["elapsed_wall_s"] = time.monotonic() - started
        try:
            unchanged = bool(input_hashes) and all(sha256_file(p) == digest for p, digest in input_hashes.items())
        except OSError:
            unchanged = False
        report["input_files_unchanged"] = unchanged
        if input_hashes and not unchanged:
            report.update(status="not_passed", input_integrity_error="task or configuration changed during run")
            code = 1
        try:
            write_new_report(args.report, report)
        except FileExistsError:
            parser.error("report appeared during execution; existing evidence was preserved")
    print(json.dumps({"status": report["status"], "report": str(args.report)}, ensure_ascii=False))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
