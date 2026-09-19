"""Build a fail-closed index of local physical calibration evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.scenario_generation.physical_evidence import content_hash


def build_index(directory: Path) -> dict:
    rows = []
    report_paths = set(directory.rglob("report.json"))
    report_paths.update(
        path for path in directory.glob("*.json")
        if not path.name.endswith((".manifest.json", ".validation.json"))
        and not path.name.startswith("evidence_index")
    )
    for report_path in sorted(report_paths):
        manifest_path = Path(str(report_path) + ".manifest.json")
        if not manifest_path.is_file():
            manifest_path = report_path.with_name("report.manifest.json")
        validation_path = Path(str(report_path) + ".validation.json")
        row = {"report": str(report_path), "status": "unknown", "issues": []}
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if content_hash(report) != manifest.get("report_sha256"):
                row["issues"].append("report hash does not match manifest")
            if report.get("runner_sha256") != manifest.get("runner_sha256"):
                row["issues"].append("runner hash does not match manifest")
            if validation_path.is_file():
                validation = json.loads(validation_path.read_text(encoding="utf-8"))
                if validation.get("report_sha256") != manifest.get("report_sha256"):
                    row["issues"].append("validation report hash does not match manifest")
                if validation.get("runner_sha256") != manifest.get("runner_sha256"):
                    row["issues"].append("validation runner hash does not match manifest")
                row.update({
                    "tier": validation.get("tier"),
                    "validated": validation.get("validated") is True,
                    "subject": validation.get("subject", report.get("subject")),
                    "scope": validation.get("scope"),
                })
            else:
                row["issues"].append("validation file is missing")
            if not row["issues"]:
                row["status"] = "verified_component_evidence"
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            row["issues"].append(f"cannot parse or verify evidence: {exc}")
        rows.append(row)
    return {
        "schema": "eai.physical_evidence_index",
        "version": 1,
        "rows": rows,
        "policy": {
            "verified_scope": "component_level_only",
            "mobile_promotion": False,
            "hardware_promotion": False,
        },
        "limitations": [
            "Index integrity is hash consistency, not measurement authentication",
            "Simulation evidence does not establish rated payload, grasp force, wind resistance, collision safety margin, or load dynamics",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_index(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(result["rows"]), "verified": sum(row["status"] == "verified_component_evidence" for row in result["rows"])}, ensure_ascii=False))
    return 0 if all(not row["issues"] for row in result["rows"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
