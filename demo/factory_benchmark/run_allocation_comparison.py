"""Freeze and execute one Factory allocation comparison through the EAI launcher."""

import argparse
import json
from pathlib import Path

from EAI.scenario_spec.geometry import provider_map, validate_connectivity, validate_geometry
from demo.benchmark.provenance import capture_request, execution_code_provenance, file_sha256, validate_execution_provenance
from demo.benchmark.runtime import build_plan, planning_template
from demo.factory_benchmark.compare_allocation import compare_scenario
from tools.scenario_generation.batch_execute import _record_attempt, run_executor
from tools.scenario_generation.common import write_json
from tools.scenario_generation.robot_dossier import dossier_snapshot_hash, robot_dossier


def summarize_attempt(method, attempt, protocol):
    report = attempt.get("report_body") or {}
    errors = []
    try:
        validate_execution_provenance(attempt, expected_sources=protocol["sources"],
                                      expected_options=protocol["options"], check_runtime_files=True)
    except (ValueError, TypeError, KeyError) as error:
        errors.append(f"provenance:{error}")
    if attempt.get("status") != "completed":
        errors.append("execution_attempt_failed")
    metrics = report.get("factory_metrics") or {}
    coverage = metrics.get("coverage", {})
    if coverage.get("validated") is False:
        errors.append("coverage_freshness_failed")
    if report.get("camera_window_validation", {}).get("validated") is False:
        errors.append("camera_window_failed")
    if not report.get("execution_validation", {}).get("validated"):
        errors.append("execution_validation_failed")
    return {
        "method": method, "status": "passed" if not errors else "failed", "violations": errors,
        "report_sha256": attempt.get("report_sha256"), "steps": report.get("steps_taken"),
        "sim_terminal_s": metrics.get("scheduling", {}).get("terminal_time_s"),
        "control_wall_s": report.get("control_loop_wall_time_s"),
        "total_process_wall_s": attempt.get("outcome", {}).get("duration_s"),
        "completed_nodes": metrics.get("scheduling", {}).get("completed_nodes", []),
        "waiting_s": metrics.get("scheduling", {}).get("waiting_time_s"),
        "max_observation_gap_s": coverage.get("max_gap_s"), "stale_sites": coverage.get("stale_sites"),
        "resource_conflicts": metrics.get("shared_resources", {}).get("reservation_conflicts"),
    }


def run_comparison(scenario_path, measurement_path, output_dir, *, candidate_budget=10000,
                   cost_model="map_route_length", steps=9000, device="cpu",
                   render_period_s=0.2, wall_timeout_s=480.0, process_timeout_s=630.0):
    scenario_path, measurement_path, output_dir = map(Path, (scenario_path, measurement_path, output_dir))
    sources = execution_code_provenance()
    scenario_digest, measurement_digest = file_sha256(scenario_path), file_sha256(measurement_path)
    comparison = compare_scenario(scenario_path, candidate_budget=candidate_budget,
                                  measurement_path=measurement_path, cost_model=cost_model)
    if file_sha256(scenario_path) != scenario_digest or file_sha256(measurement_path) != measurement_digest:
        raise ValueError("comparison inputs changed while checking admission")
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "comparison.json", comparison)
    options = {"steps": steps, "device": device, "transport_mode": "none",
               "render_period_s": render_period_s, "wall_timeout_s": wall_timeout_s}
    protocol = {
        "schema": "eai.factory_allocation_comparison_run", "version": 2,
        "stage": "engineering_single_layout", "method_order": ["fixed", "greedy", "optimized"],
        "sources": sources, "options": options, "process_timeout_s": process_timeout_s,
        "candidate_evaluation_budget": candidate_budget, "cost_model": cost_model,
        "input_sha256": comparison["input_sha256"],
        "comparison_sha256": file_sha256(output_dir / "comparison.json"),
        "scenario_sha256": scenario_digest, "measurements_sha256": measurement_digest, "scenarios": {},
        "limitations": ["One engineering world; no statistical ranking or held-out evaluation",
                        "Candidate evaluation budgets do not equal CPU wall-time budgets",
                        "Methods share the frozen candidate information and cost table",
                        "Source or asset drift invalidates this comparison; no silent retries"],
    }
    occupancy = provider_map(scene_key="factory")
    for method, scenario in comparison["compiled_scenarios"].items():
        path = output_dir / f"{method}.scenario.json"
        write_json(path, scenario)
        try:
            validate_geometry(scenario, occupancy)
            validate_connectivity(scenario, occupancy)
            build_plan(scenario, occupancy, planning_template("factory", occupancy))
        except ValueError as error:
            protocol["scenarios"][method] = {"sha256": file_sha256(path), "preflight_error": str(error)}
        else:
            request = capture_request(path, options)
            write_json(output_dir / f"{method}.request.json", request)
            protocol["scenarios"][method] = {
                "sha256": file_sha256(path), "request_sha256": request["sha256"],
                "request_file_sha256": file_sha256(output_dir / f"{method}.request.json"),
            }
    if execution_code_provenance() != sources:
        raise ValueError("execution sources changed while freezing the comparison")
    protocol_path = output_dir / "protocol.json"
    write_json(protocol_path, protocol)
    protocol_digest = file_sha256(protocol_path)
    runtime_conditions = None
    results = []
    for method in protocol["method_order"]:
        if execution_code_provenance() != sources:
            raise ValueError("execution sources changed between paired methods; existing results were preserved")
        if (file_sha256(protocol_path) != protocol_digest
                or file_sha256(output_dir / "comparison.json") != protocol["comparison_sha256"]):
            raise ValueError("frozen protocol or comparison changed between paired methods")
        metadata = protocol["scenarios"].get(method)
        if metadata is None or metadata.get("preflight_error"):
            result = {"method": method, "status": "not_executable",
                      "reason": (metadata["preflight_error"] if metadata else
                                 f"allocation:{comparison['methods'][method]['status']}")}
        else:
            scenario_path = output_dir / f"{method}.scenario.json"
            if file_sha256(scenario_path) != metadata["sha256"]:
                raise ValueError("compiled scenario changed after freezing")
            scenario = comparison["compiled_scenarios"][method]
            dossiers = {robot["id"]: robot_dossier(robot["type"], attachments=[
                attachment["type"] for attachment in robot["attachments"]]) for robot in scenario["robots"]}
            request_path = output_dir / f"{method}.request.json"
            request = json.loads(request_path.read_text())
            if (file_sha256(request_path) != metadata["request_file_sha256"]
                    or request.get("sha256") != capture_request(scenario_path, options)["sha256"]):
                raise ValueError("execution request changed after freezing")
            report_path = output_dir / f"{method}.report.json"
            print(json.dumps({"method": method, "state": "starting"}), flush=True)
            outcome, report = run_executor(
                scenario_path, report_path, steps=steps, timeout_s=process_timeout_s,
                device=device, transport_mode="none", request_path=request_path,
                render_period_s=render_period_s, wall_timeout_s=wall_timeout_s)
            attempt = _record_attempt(
                instance_id=f"factory_comparison_{method}", scenario_sha256=metadata["sha256"],
                robot_dossier_snapshot_sha256=dossier_snapshot_hash(dossiers), report_path=report_path,
                transport_mode="none", steps=steps, device=device, timeout_s=process_timeout_s,
                outcome=outcome, report=report, request=request,
                protocol_context={"paired_protocol_sha256": file_sha256(protocol_path), "method": method})
            write_json(output_dir / f"{method}.attempt.json", attempt)
            result = summarize_attempt(method, attempt, protocol)
            runtime = (report or {}).get("execution_provenance", {}).get("runtime")
            if runtime:
                conditions = {key: runtime.get(key) for key in ("files", "effective", "usd_roots", "occupancy_sha256")}
                if runtime_conditions is None:
                    runtime_conditions = conditions
                elif conditions != runtime_conditions:
                    result["status"] = "failed"
                    result["violations"].append("paired_runtime_conditions_differ")
        results.append(result)
        write_json(output_dir / "results.json", results)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-budget", type=int, default=10000)
    parser.add_argument("--cost-model", choices=("unit", "map_route_length"), default="map_route_length")
    parser.add_argument("--steps", type=int, default=9000)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--render-period-s", type=float, default=0.2)
    parser.add_argument("--wall-timeout-s", type=float, default=480.0)
    parser.add_argument("--process-timeout-s", type=float, default=630.0)
    args = parser.parse_args(argv)
    results = run_comparison(
        args.scenario, args.measurements, args.output_dir, candidate_budget=args.candidate_budget,
        cost_model=args.cost_model, steps=args.steps, device=args.device,
        render_period_s=args.render_period_s, wall_timeout_s=args.wall_timeout_s,
        process_timeout_s=args.process_timeout_s)
    return int(any(result["status"] != "passed" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
