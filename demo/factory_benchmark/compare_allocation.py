"""Compare fixed, greedy and bounded exact Factory allocations without Isaac."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from EAI.scenario_spec import load_scenario
from demo.benchmark.allocation import (
    compare_allocators, compile_assignment_plan, reallocation_certificate,
)
from demo.factory_benchmark.tasks import factory_task_requirements, validate_factory_scenario
from tools.scenario_generation.capability_profile import profile_for_dossier
from tools.scenario_generation.robot_dossier import robot_dossier


def compare_scenario(source: Path, *, candidate_budget: int = 10000,
                     measurement_path: Path | None = None, cost_model: str = "unit") -> dict:
    data = load_scenario(source)
    task_id = data.get("benchmark_task_id")
    validate_factory_scenario(data, task_id)
    if data.get("fault_events") or any(node.get("recovery_for") for node in data["phase_graph"]["nodes"]):
        raise ValueError("conditional recovery allocation requires a separate recourse comparison")
    requirements = factory_task_requirements(task_id)
    if {item["id"] for item in requirements} != {node["id"] for node in data["phase_graph"]["nodes"]}:
        raise ValueError("comparison requires an exact registered Factory phase graph")
    from tools.scenario_generation.record_configuration_microtests import task_scope
    nodes = {node["id"]: node for node in data["phase_graph"]["nodes"]}
    for requirement in requirements:
        for protocol in requirement.get("required_protocols", ()):
            protocol["scope"] = task_scope(data, nodes[requirement["id"]])
    measured_by_configuration = {}
    if measurement_path is not None:
        from tools.scenario_generation.configuration_microtest import load_records, measurement_for_profile
        for record in load_records(measurement_path):
            measured_by_configuration.setdefault(record["configuration_id"], []).append(measurement_for_profile(record))
    profiles, fleet = {}, []
    for robot in data["robots"]:
        dossier = robot_dossier(robot["type"], attachments=[item["type"] for item in robot["attachments"]])
        declared_profile = profile_for_dossier(dossier)
        if robot["controller"]["cfg"] != declared_profile["identity"]["controller_cfg"]:
            raise ValueError("comparison requires evidence for the selected controller configuration")
        profile = profile_for_dossier(dossier, measured_by_configuration.get(
            declared_profile["identity"]["configuration_id"], ()))
        profile_key = profile["identity"]["configuration_id"]
        profiles[profile_key] = profile
        fleet.append({"id": robot["id"], "robot_key": robot["type"], "profile_key": profile_key})
    fixed = {node["id"]: node.get("agents") or [node["preferred_agent"]]
             for node in data["phase_graph"]["nodes"]}
    if cost_model == "map_route_length":
        from demo.factory_benchmark.route_costs import estimate_route_costs
        cost_details = estimate_route_costs(data, requirements)
    elif cost_model == "unit":
        cost_details = {"model": "unit_assignment_count", "unit": "assignments",
                        "estimated_costs": None, "unavailable_assignments": None}
    else:
        raise ValueError(f"unknown allocation cost model {cost_model!r}")
    comparison = compare_allocators(requirements, fleet, profiles, fixed,
                                    estimated_costs=cost_details["estimated_costs"],
                                    unavailable_assignments=cost_details["unavailable_assignments"],
                                    max_candidate_evaluations=candidate_budget)
    reallocation = reallocation_certificate(
        requirements, fleet, profiles, max_candidate_evaluations=candidate_budget)
    compiled = {}
    for method, assignment in comparison["methods"].items():
        if assignment["status"] == "feasible":
            scenario = compile_assignment_plan(data, assignment)
            validate_factory_scenario(scenario, task_id)
            compiled[method] = scenario
    return {
        **comparison, "reallocation_certificate": reallocation,
        "benchmark_task_id": task_id,
        "scenario_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "fleet": fleet, "profiles": profiles, "compiled_scenarios": compiled,
        "cost_model_details": cost_details,
        "limitations": [
            "catalog evidence is a configuration declaration; radar axes remain unknown",
            "declared cost estimates do not establish travel time, freshness or physical performance",
            "identical candidate evaluation budgets do not imply identical CPU wall time",
            "compiled assignments require separate geometry and Isaac execution validation",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-budget", type=int, default=10000)
    parser.add_argument("--cost-model", choices=("unit", "map_route_length"), default="unit")
    parser.add_argument("--measurements", type=Path,
                        help="Validated configuration micro-test records used for hard admission")
    args = parser.parse_args(argv)
    report = compare_scenario(args.scenario, candidate_budget=args.candidate_budget,
                              measurement_path=args.measurements, cost_model=args.cost_model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(args.output), "methods": {
        method: {"status": item["status"], "objective": item["objective"],
                 "optimality_proven": item["optimality_proven"]}
        for method, item in report["methods"].items()
    }}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
