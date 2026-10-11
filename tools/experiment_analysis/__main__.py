"""python -m tools.experiment_analysis --help"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from .collector import collector_handoff, bind_outcomes
from .batch import read_sources
from .bundle import export_batch, export_report, output_directory as _output, verify_bundle, write_json as _write
from .engine import analyze
from .planning import readiness
from .registration import freeze
from .schema import AnalysisError, read_json
from .synthetic import generate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frozen-design descriptive analysis; never grants paper release qualification")
    commands = parser.add_subparsers(dest="command", required=True)
    synth = commands.add_parser("synthetic", help="generate raw synthetic controls, calculate, and check analytic answers")
    synth.add_argument("--output", required=True)
    synth.add_argument("--seed", type=int, default=773)
    synth.add_argument("--instances", type=int, default=40)
    synth.add_argument("--resamples", type=int, default=400)
    compute = commands.add_parser("compute", help="calculate externally registered raw scalar/event outcomes")
    compute.add_argument("--design", required=True)
    compute.add_argument("--data", required=True)
    compute.add_argument("--output", required=True)
    ready = commands.add_parser("readiness", help="inspect frozen design and continuation counts before execution")
    ready.add_argument("--design", required=True)
    handoff = commands.add_parser("collector-handoff", help="verify a sealed collector run and export all attempt identities")
    handoff.add_argument("--run-dir", required=True)
    handoff.add_argument("--output", required=True)
    binding = commands.add_parser("bind-outcomes", help="bind external evaluator outcomes to exact sealed collector attempts")
    binding.add_argument("--design", required=True)
    binding.add_argument("--run-dir", required=True)
    binding.add_argument("--outcomes", required=True)
    binding.add_argument("--output", required=True)
    register = commands.add_parser("freeze", help="freeze design, batch plans, context, and ordered retry policy before collection")
    register.add_argument("--design", required=True)
    register.add_argument("--schedule", required=True)
    register.add_argument("--output", required=True)
    merge = commands.add_parser("assemble", help="verify sealed batches, select registered attempts, and export a result bundle")
    merge.add_argument("--design", required=True)
    merge.add_argument("--registration", required=True)
    merge.add_argument("--sources", required=True)
    merge.add_argument("--output", required=True)
    verify = commands.add_parser("verify", help="read-only bundle consistency and optional pinned-source recomputation")
    verify.add_argument("--bundle", required=True)
    verify.add_argument("--recompute", action="store_true")
    batch_control = commands.add_parser("synthetic-batch", help="freeze then run two synthetic collector batches and verify their result bundle")
    batch_control.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            registration = freeze(read_json(args.design), read_json(args.schedule))
            root = _output(args.output)
            _write(root / "registration.json", registration)
            print(json.dumps({"output": str(root), "registration_digest": registration["registration_digest"], "publishable": False}))
            return 0
        if args.command == "assemble":
            source_path = Path(args.sources).resolve()
            sources = read_sources(read_json(source_path), base_dir=source_path.parent)
            root = export_batch(args.output, read_json(args.design), read_json(args.registration), sources)
            audit = read_json(root / "batch_audit.json")
            print(json.dumps({"output": str(root), "collection_status": audit["status"], "selected_observations": audit["selected_observations"], "publishable": False}))
            return 0
        if args.command == "verify":
            print(json.dumps(verify_bundle(args.bundle, recompute=args.recompute), sort_keys=True))
            return 0
        if args.command == "synthetic-batch":
            from .synthetic_batch import run
            print(json.dumps(run(args.output), sort_keys=True))
            return 0
        if args.command == "readiness":
            report = readiness(read_json(args.design))
            print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
            return 0 if report["status"] == "ready_for_collection" else 2
        if args.command == "collector-handoff":
            report = collector_handoff(args.run_dir)
            root = _output(args.output)
            _write(root / "handoff.json", report)
            print(json.dumps({"output": str(root), "attempts": len(report["attempts"]), "publishable": False}))
            return 0
        if args.command == "bind-outcomes":
            design, outcomes = read_json(args.design), read_json(args.outcomes)
            data = bind_outcomes(design, outcomes, args.run_dir)
            report = analyze(design, data)
            handoff = collector_handoff(args.run_dir)
            if handoff["seal_sha256"] != outcomes["collector_seal_sha256"]:
                raise AnalysisError("collector changed before export")
            root = _output(args.output)
            _write(root / "design.json", design)
            _write(root / "data.json", data)
            _write(root / "evaluated_outcomes.json", outcomes)
            _write(root / "handoff.json", handoff)
            export_report(root, report, bundle_type="bound")
            print(json.dumps({"output": str(root), "records": report["records"], "publishable": False}))
            return 0
        if args.command == "synthetic":
            design, data, expected = generate(seed=args.seed, instances=args.instances, resamples=args.resamples)
        else:
            design, data = read_json(args.design), read_json(args.data)
            expected = None
        report = analyze(design, data)
        root = _output(args.output)
        _write(root / "design.json", design)
        _write(root / "data.json", data)
        _write(root / "readiness.json", readiness(design))
        if expected is not None:
            checks = []
            for row in report["results"]:
                value = expected[row["estimand_id"]]
                if isinstance(value, dict):
                    value = value[row["system_id"]]
                checks.append({"estimand": row["estimand_id"], "system": row["system_id"], "expected": value,
                               "actual": row["estimate"], "passed": row["estimate"] is not None and math.isclose(value, row["estimate"], abs_tol=1e-12)})
            _write(root / "analytic_checks.json", {"passed": all(c["passed"] for c in checks), "checks": checks, "publishable": False})
        export_report(root, report, bundle_type="synthetic" if expected is not None else "compute")
        print(json.dumps({"output": str(root), "records": report["records"], "results": len(report["results"]),
                          "N/A": sum(r["status"] == "N/A" for r in report["results"]), "publishable": False}))
        return 1 if expected is not None and not all(c["passed"] for c in checks) else 0
    except (AnalysisError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"analysis rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
