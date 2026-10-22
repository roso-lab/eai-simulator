"""Exact differential comparison; qualification is limited to supplied traces.

Callers run the same branches in fresh reference processes, permuted slots and
perturbed sibling schedules, then supply traces containing controls, observations,
state digests and outcomes. The result is evidence, not a native-physics claim.
"""
from .models import canonical_digest
import argparse
import json
from pathlib import Path


def compare_isolation_traces(reference, candidate):
    if not isinstance(reference, dict) or not reference or not isinstance(candidate, dict):
        raise ValueError("nonempty branch trace mappings required")
    differences = []
    if set(reference) != set(candidate):
        differences.append({"reason": "branch_set_mismatch"})
    for branch in sorted(set(reference) & set(candidate)):
        expected, observed = reference[branch], candidate[branch]
        if not isinstance(expected, list) or not isinstance(observed, list) or not expected or not observed:
            raise ValueError("branch traces require nonempty frame lists")
        if len(expected) != len(observed):
            differences.append({"branch_id": branch, "reason": "frame_count_mismatch"})
        for index, (first, second) in enumerate(zip(expected, observed)):
            for frame in (first, second):
                if not isinstance(frame, dict) or "state" not in frame or "row" not in frame:
                    raise ValueError("trace frame requires state digest and observed row")
                if not frame["state"] or any(name not in frame["row"] for name in (
                        "simulator_step", "action", "applied_action", "observation")):
                    raise ValueError("trace frame lacks required state/control/observation channels")
            if canonical_digest(first) != canonical_digest(second):
                differences.append({"branch_id": branch, "frame": index, "reason": "trace_mismatch"})
                break
    return {"schema": "heterocoord.vectorized_differential.v1", "passed": not differences,
            "reference_digest": canonical_digest(reference), "candidate_digest": canonical_digest(candidate),
            "branches": len(reference), "differences": differences,
            "claim_scope": "supplied_registered_state_control_observation_traces",
            "process_failure_isolation": False}


def qualify_trace_files(reference_path, candidate_path, output_path=None, *, metadata=None):
    """Compare a fresh-process trace with a managed vectorized trace."""
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    candidate = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
    meta = dict(metadata or {})
    if meta.get("reference_execution_mode") not in (None, "fresh_process"):
        raise ValueError("reference_execution_mode must be fresh_process")
    if meta.get("candidate_execution_backend") not in (None, "vectorized_session"):
        raise ValueError("candidate_execution_backend must be vectorized_session")
    report = compare_isolation_traces(reference, candidate)
    report.update({"declared_provenance": meta, "provenance_verified": False,
                   "qualification_status": "trace_comparison_passed" if report["passed"] else "failed"})
    if output_path is not None:
        Path(output_path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare fresh-process and vectorized branch traces")
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report = qualify_trace_files(args.reference, args.candidate, args.output)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
