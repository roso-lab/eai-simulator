"""Freeze and run Factory printed-label conditions with independent pixel evaluation."""

import argparse
import json
from pathlib import Path

from EAI.scenario_spec import load_scenario
from EAI.scenario_spec.geometry import provider_map, validate_connectivity, validate_geometry
from demo.benchmark.marker_detection import MARKER_SIDE_M
from demo.benchmark.provenance import (
    capture_request, execution_code_provenance, file_sha256, validate_execution_provenance,
)
from demo.benchmark.runtime import build_plan, planning_template
from demo.factory_benchmark.inspect_inventory import evaluate_inventory
from tools.scenario_generation.batch_execute import _record_attempt, run_executor
from tools.scenario_generation.common import write_json
from tools.scenario_generation.robot_dossier import dossier_snapshot_hash, robot_dossier


CONDITIONS = {
    "visible17": {"inventory_label_id": 17, "inventory_label_state": "visible"},
    "covered17": {"inventory_label_id": 17, "inventory_label_state": "covered"},
    "absent17": {"inventory_label_id": 17, "inventory_label_state": "absent"},
    "visible23": {"inventory_label_id": 23, "inventory_label_state": "visible"},
}
DISPATCH_CONDITIONS = {
    f"{mode}{marker_id}": {"inventory_label_id": marker_id, "inventory_label_state": "visible",
                          "inventory_dispatch_mode": mode, "inventory_message_delay_s": 60.0,
                          "inventory_dispatch_policy": "immediate", "inventory_dispatch_wait_s": 65.0}
    for marker_id in (17, 23) for mode in ("shared", "withheld", "delayed")
}
# These are reference-baseline conditions, not participant task requirements.
DISPATCH_CONDITIONS.update({
    "short_delayed23": {
        "inventory_label_id": 23, "inventory_label_state": "visible",
        "inventory_dispatch_mode": "delayed", "inventory_message_delay_s": 5.0,
        "inventory_dispatch_policy": "immediate", "inventory_dispatch_wait_s": 65.0,
    },
    "waiting23": {
        "inventory_label_id": 23, "inventory_label_state": "visible",
        "inventory_dispatch_mode": "delayed", "inventory_message_delay_s": 60.0,
        "inventory_dispatch_policy": "wait_for_message", "inventory_dispatch_wait_s": 65.0,
    },
})


def paired_runtime_signature(report):
    runtime = report["execution_provenance"]["runtime"]
    variant = report["inventory_detection"]["world_variant"]
    if runtime["files"].get(variant["path"]) != variant["sha256"]:
        raise ValueError("inventory variant is missing from the runtime file closure")
    return {"files": {path: digest for path, digest in runtime["files"].items() if path != variant["path"]},
            "usd_roots": sorted(path for path in runtime["usd_roots"] if path != variant["path"]),
            "effective": runtime["effective"], "occupancy_sha256": runtime["occupancy_sha256"],
            "plan_sha256": runtime["plan_sha256"],
            "marker_side_m": variant["marker_side_m"], "surface_support": variant["surface_support"],
            "base_sha256": variant["base_sha256"]}


def run_matrix(scenario_path, output_dir, *, conditions=None, steps=9000, device="cpu",
               render_period_s=0.2, wall_timeout_s=480.0, process_timeout_s=630.0, dispatch=False):
    scenario_path, output_dir = Path(scenario_path), Path(output_dir)
    available_conditions = DISPATCH_CONDITIONS if dispatch else CONDITIONS
    conditions = list(available_conditions) if conditions is None else list(conditions)
    if not conditions or len(set(conditions)) != len(conditions) or any(name not in available_conditions for name in conditions):
        raise ValueError("inventory conditions must be unique, known and nonempty")
    sources = execution_code_provenance()
    source_digest = file_sha256(scenario_path)
    scenario = load_scenario(scenario_path)
    if scenario.get("scene_key") != "factory" or scenario.get("benchmark_task_id") != "F-INS-01":
        raise ValueError("inventory matrix requires Factory F-INS-01")
    occupancy = provider_map(scene_key="factory")
    validate_geometry(scenario, occupancy)
    validate_connectivity(scenario, occupancy)
    build_plan(scenario, occupancy, planning_template("factory", occupancy))
    if dispatch:
        from demo.factory_benchmark.inventory_dispatch import (
            PUBLIC_RULES, preflight_dispatch, validate_dispatch_scene_bindings,
        )
        preflight_dispatch(scenario, occupancy, planning_template("factory", occupancy))
        validate_dispatch_scene_bindings(scenario)
    dossiers = {robot["id"]: robot_dossier(robot["type"], attachments=[
        item["type"] for item in robot["attachments"]]) for robot in scenario["robots"]}
    options = {"steps": steps, "device": device, "transport_mode": "none", "render_period_s": render_period_s,
               "wall_timeout_s": wall_timeout_s, "archive_camera_frames": True}
    output_dir.mkdir(parents=True, exist_ok=False)
    protocol = {"schema": "eai.factory_inventory_matrix", "version": 5 if dispatch else 3,
                "sources": sources, "scenario_sha256": source_digest, "options": options,
                "conditions": {name: available_conditions[name] for name in conditions}, "requests": {},
                "marker_side_m": MARKER_SIDE_M, "process_timeout_s": process_timeout_s,
                "localization_policy": "fixed_per_view_coordinate_median", "localization_tolerance_m": 0.1,
                "limitations": ["One engineering world; failures retained; no statistical recognition rate",
                                "Printed identity only; no natural-object recognition or decision-use claim"]}
    if dispatch:
        protocol.update(public_dispatch_rules=PUBLIC_RULES,
                        business_success="measured post-decision arrival at the station for the evaluator-only true ID",
                        failure_policy="wrong default routes are retained task failures; a correct guess counts as success",
                        limitations=["One engineering world; no recognition reliability or heterogeneity-necessity claim",
                                     "Runs do not restore simulator state or guarantee paired sensor noise"])
    for name in conditions:
        directory = output_dir / name
        directory.mkdir()
        path = directory / "scenario.json"
        path.write_bytes(scenario_path.read_bytes())
        if file_sha256(path) != source_digest:
            raise ValueError("inventory scenario changed during freeze")
        request = capture_request(path, {**options, **available_conditions[name]})
        write_json(directory / "request.json", request)
        protocol["requests"][name] = file_sha256(directory / "request.json")
    if execution_code_provenance() != sources:
        raise ValueError("inventory sources changed during freeze")
    protocol_path = output_dir / "protocol.json"
    write_json(protocol_path, protocol)
    protocol_digest = file_sha256(protocol_path)
    results, first_runtime = [], None
    for name in conditions:
        if execution_code_provenance() != sources or file_sha256(protocol_path) != protocol_digest:
            raise ValueError("frozen inventory sources or protocol changed; existing results were preserved")
        directory = output_dir / name
        path, request_path, report_path = [directory / filename for filename in
                                          ("scenario.json", "request.json", "report.json")]
        if file_sha256(path) != source_digest or file_sha256(request_path) != protocol["requests"][name]:
            raise ValueError("frozen inventory input changed")
        request = json.loads(request_path.read_text())
        print(json.dumps({"condition": name, "state": "starting"}), flush=True)
        outcome, report = run_executor(path, report_path, timeout_s=process_timeout_s, request_path=request_path,
                                       **options, **available_conditions[name])
        attempt = _record_attempt(
            instance_id=name, scenario_sha256=source_digest, robot_dossier_snapshot_sha256=dossier_snapshot_hash(dossiers),
            report_path=report_path, transport_mode="none", steps=steps, device=device,
            timeout_s=process_timeout_s, outcome=outcome, report=report, request=request,
            protocol_context={"paired_protocol_sha256": protocol_digest, "condition": name})
        write_json(directory / "attempt.json", attempt)
        result = {"condition": name, "execution_status": attempt["status"], "evidence_validated": False,
                  "business_task_validated": False, "violations": []}
        try:
            validate_execution_provenance(attempt, expected_sources=sources,
                                          expected_options=request["options"], check_runtime_files=True)
            if attempt["status"] != "completed":
                result["violations"].append("execution_attempt_failed")
            signature = paired_runtime_signature(report)
            if first_runtime is None:
                first_runtime = signature
            elif signature != first_runtime:
                raise ValueError("paired non-intervention runtime conditions differ")
            evaluation = evaluate_inventory(report, scenario=scenario)
            evaluation["sources"] = {"scenario_sha256": source_digest, "report_sha256": file_sha256(report_path),
                                     "attempt_sha256": file_sha256(directory / "attempt.json")}
            write_json(directory / "evaluation.json", evaluation)
            result.update(evidence_validated=True, frames=evaluation["frames"], fused_views=evaluation["fused_views"],
                          all_views_localized=evaluation["all_views_localized"],
                          missing_view_nodes=evaluation["missing_view_nodes"],
                          negative_condition_observed=evaluation["negative_condition_observed"])
            condition_validated = (evaluation["all_views_localized"] if available_conditions[name]["inventory_label_state"] == "visible"
                                   else evaluation["negative_condition_observed"])
            result["condition_validated"] = condition_validated
            if not condition_validated:
                result["violations"].append("inspection_view_condition_failed")
            if dispatch:
                from demo.factory_benchmark.inspect_dispatch import evaluate_dispatch
                dispatch_evaluation = evaluate_dispatch(report, scenario=scenario)
                write_json(directory / "dispatch.evaluation.json", dispatch_evaluation)
                result["dispatch"] = dispatch_evaluation
                result["business_task_validated"] = dispatch_evaluation["dispatch_success"]
                if (not dispatch_evaluation.get("participant_action_trace_validated")
                        and not dispatch_evaluation.get("decision_replay_validated")):
                    result["violations"].append("dispatch_decision_not_validated")
                if (dispatch_evaluation.get("baseline_replay_required")
                        and dispatch_evaluation.get("decision_replay_validated") is not True):
                    result["violations"].append("dispatch_baseline_replay_not_validated")
        except (ValueError, TypeError, KeyError, OSError) as error:
            result["violations"].append(str(error))
        results.append(result)
        write_json(output_dir / "results.json", results)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--dispatch", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--conditions", nargs="+", choices=[*CONDITIONS, *DISPATCH_CONDITIONS])
    parser.add_argument("--steps", type=int, default=9000)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--render-period-s", type=float, default=0.2)
    parser.add_argument("--wall-timeout-s", type=float, default=480.0)
    parser.add_argument("--process-timeout-s", type=float, default=630.0)
    args = parser.parse_args(argv)
    if args.scenario is None:
        args.scenario = Path(__file__).parent / "scenarios" / (
            "inspection_dispatch_01.json" if args.dispatch else "inspection_01.json")
    results = run_matrix(args.scenario, args.output_dir, conditions=args.conditions, steps=args.steps,
                         device=args.device, render_period_s=args.render_period_s,
                         wall_timeout_s=args.wall_timeout_s, process_timeout_s=args.process_timeout_s,
                         dispatch=args.dispatch)
    return int(any(result["violations"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
