"""python -m tools.experiment_analysis --help"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

from .collector import collector_handoff, bind_outcomes
from .engine import analyze
from .planning import readiness
from .schema import AnalysisError, read_json
from .synthetic import generate


def _write(path: Path, data: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def _output(path: str) -> Path:
    target = Path(path).resolve()
    target.mkdir(parents=True, exist_ok=False)
    return target


def _md(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _exports(root: Path, report: dict) -> None:
    _write(root / "report.json", report)
    fields = ["estimand_id", "system_id", "kind", "status", "estimate", "ci_low", "ci_high", "interval_status", "reasons", "provenance", "publishable"]
    with (root / "results.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in report["results"]:
            out = {key: row[key] for key in fields[:5]}
            out.update(ci_low=row["interval"]["low"], ci_high=row["interval"]["high"], interval_status=row["interval"]["status"],
                       reasons=";".join(row["reasons"]), provenance=report["qualification"], publishable=False)
            # Spreadsheet applications must not interpret caller-supplied IDs
            # as formulas when a descriptive CSV is opened interactively.
            writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for k, v in out.items()})
    lines = ["# Descriptive calculation report", "", f"Source: {report['qualification']}. Publishable: false.",
             "External evaluator and protocol release verification remain required.", "",
             f"Design SHA-256: `{report['design_digest']}`", f"Dataset SHA-256: `{report['dataset_digest']}`", "",
             "| Estimand | System | Status | Estimate | 95% cluster interval | Reason |", "|---|---|---|---:|---|---|"]
    for row in report["results"]:
        ci = row["interval"]
        interval = "N/A: " + str(ci["reason"]) if ci["status"] != "computed" else f"[{ci['low']:.6g}, {ci['high']:.6g}]"
        point = "N/A" if row["estimate"] is None else f"{row['estimate']:.6g}"
        lines.append("| " + " | ".join(_md(v) for v in (row["estimand_id"], row["system_id"], row["status"], point, interval, ";".join(row["reasons"]))) + " |")
    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.iterdir()) if p.is_file()}
    _write(root / "output_manifest.json", {"schema": "eai.analysis.v1.output_manifest", "files": files, "publishable": False})


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
    args = parser.parse_args(argv)
    try:
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
            _exports(root, report)
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
        _exports(root, report)
        print(json.dumps({"output": str(root), "records": report["records"], "results": len(report["results"]),
                          "N/A": sum(r["status"] == "N/A" for r in report["results"]), "publishable": False}))
        return 1 if expected is not None and not all(c["passed"] for c in checks) else 0
    except (AnalysisError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"analysis rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
