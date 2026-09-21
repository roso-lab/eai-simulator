"""Reproduce author-only audit sidecars for maintained diagnostic scenarios.

No simulator is launched. Indoor geometry audits require local map products;
Factory audits cover schema and task design, not physical execution.
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path

from .provenance import file_sha256


ROOT = Path(__file__).resolve().parents[2]
MAINTAINED_SCENARIOS = (
    "demo/factory_benchmark/scenarios/inspection_dispatch_01.json",
    "demo/factory_benchmark/scenarios/traffic_01.json",
    "demo/factory_benchmark/scenarios/coverage_01.json",
    "demo/factory_benchmark/scenarios/coverage_01_dropout.json",
    "demo/factory_benchmark/scenarios/vertical_stair_01.json",
    "demo/warehouse_benchmark/scenarios/business_logistics_01.json",
    "demo/modern_house_benchmark/scenarios/home_recovery_01.json",
)


def _portable(value):
    """Keep local asset fingerprints without embedding this checkout's path."""
    if isinstance(value, dict):
        return {_portable(key): _portable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_portable(item) for item in value]
    if isinstance(value, str) and value.startswith(str(ROOT) + "/"):
        return str(Path(value).relative_to(ROOT))
    return value


def audit_scenario(path: Path) -> dict:
    from EAI.scenario_spec import load_scenario

    path = Path(path).resolve()
    data = load_scenario(path)
    track = data.get("benchmark_task_id")
    if data["scene_key"] == "factory":
        from demo.factory_benchmark.tasks import review_factory_task_design
        design = review_factory_task_design(data, track)
        admission = {"validated": True, "schema": "validated",
                     "scope": "schema_and_factory_task_contract_only"}
        granularity = ("limited_control" if track == "F-INS-01" else
                       "platform_locomotion" if track == "F-VERT-01" else "fixed_dag_allocation")
    else:
        from .scene_challenge import audit_generated_scene_scenario, review_task_design
        package, expected = {
            "warehouse_nvd": ("warehouse_benchmark", "W-DAG-01"),
            "modern_house_nvd": ("modern_house_benchmark", "MH-DAG-01"),
        }[data["scene_key"]]
        if track != expected:
            raise ValueError(f"expected diagnostic track {expected}, got {track!r}")
        scenario = importlib.import_module(f"demo.{package}.scenario")
        admission = audit_generated_scene_scenario(data)
        design = review_task_design(data, scenario.TASK_PROFILE, scenario.TASK_BLUEPRINT)
        granularity = "fixed_dag_allocation"
    if not admission["validated"] or not design["validated"]:
        raise ValueError(f"diagnostic scenario audit failed: {path}")
    return _portable({
        "schema": "eai.benchmark.diagnostic_scenario_audit", "version": 1,
        "scenario_file": path.name, "scenario_sha256": file_sha256(path),
        "track_id": track, "diagnostic_granularity": granularity,
        "visibility": "author_only", "execution_validated": False, "scoreable": False,
        "admission": admission, "task_design_review": design,
    })


def write_audit(path: Path) -> Path:
    result = audit_scenario(path)
    sidecar = Path(path).with_suffix(".audit.json")
    sidecar.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                       encoding="utf-8")
    return sidecar


def check_audit(path: Path) -> None:
    expected = audit_scenario(path)
    sidecar = Path(path).with_suffix(".audit.json")
    if json.loads(sidecar.read_text(encoding="utf-8")) != expected:
        raise ValueError(f"stale or changed diagnostic sidecar: {sidecar}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", type=Path,
                        help="Defaults to the maintained diagnostic fixtures.")
    parser.add_argument("--write", action="store_true", help="Regenerate sidecars; default is read-only verification.")
    args = parser.parse_args(argv)
    for path in args.scenarios or [ROOT / item for item in MAINTAINED_SCENARIOS]:
        (write_audit if args.write else check_audit)(path)
        print(f"{'written' if args.write else 'verified'}: {path.with_suffix('.audit.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
