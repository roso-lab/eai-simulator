"""Reproducible, bounded Restaurant public-policy diagnostic.

Run with ``python -m demo.benchmark.goal_restaurant_public_run --help``.
The preflight path does not import Isaac. A successful process is not a
successful task, an oracle certificate, or a release qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import traceback

from .goal_restaurant_fleet import CLOSED_VALIDATION_FLAGS


ROOT = Path(__file__).resolve().parents[2]


def _write(root, name, value):
    (root / name).write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _positive_integer(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--products-root", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True, help="new directory; never overwritten")
    result.add_argument("--episode-id", required=True)
    result.add_argument("--communication-contract", type=Path, required=True,
                        help="explicit shared-pool JSON; no implicit diagnostic capacities")
    result.add_argument("--preflight-only", action="store_true")
    result.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    result.add_argument("--seed", type=int, default=17)
    result.add_argument("--warmup-ticks", type=_positive_integer, default=240)
    result.add_argument("--max-ticks", type=_positive_integer, default=4200)
    return result


def _source_snapshot():
    from .goal_restaurant_fleet import restaurant_fleet_source_manifest
    from demo.restaurant_benchmark import runtime_probe

    sources = {**runtime_probe._probe_source_files(),
               **restaurant_fleet_source_manifest()["source_files"]}
    for directory in ("demo/benchmark", "demo/restaurant_benchmark"):
        for path in (ROOT / directory).glob("*.py"):
            sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return sources


def prepare_request(args):
    """Validate static inputs before any simulator startup or directory writes."""
    from .communication import validate_communication_contract
    from .goal_driven_tasks import build_goal_task_package
    from .goal_restaurant_conditions import build_restaurant_task_conditions
    from .goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
    from .goal_restaurant_fleet import build_restaurant_full_fleet_selection

    if not args.episode_id.strip():
        raise ValueError("episode id must be nonempty")
    contract = build_restaurant_deployment_v2()
    communication = validate_communication_contract(json.loads(
        args.communication_contract.read_text(encoding="utf-8")))
    if not {"backhaul_uplink", "backhaul_downlink", "p2p"} <= set(communication.get("pools", {})):
        raise ValueError("explicit uplink, downlink and p2p shared pools are required")
    package = build_goal_task_package("restaurant")
    conditions = build_restaurant_task_conditions(
        public_task=contract["public_task"], hidden_evaluator=package["hidden_evaluator"],
        communication_contract=communication, deployment_companion=contract["deployment"])
    request = {
        "schema": "eai.restaurant.public_policy_run", "version": 1,
        "scope": "bounded_public_policy_diagnostic_not_qualified_baseline",
        "episode_id": args.episode_id, "public_task": contract["public_task"],
        "deployment": contract["deployment"], "participant_api": contract["participant_api"],
        "selection": build_restaurant_full_fleet_selection(),
        "communication_contract": communication, "task_conditions": conditions,
        "source_sha256": _source_snapshot(), "device": args.device, "seed": args.seed,
        "products_root": str(args.products_root.resolve()), "warmup_ticks": args.warmup_ticks,
        "max_ticks": args.max_ticks,
        "participant_policy": "public_nearest_distinct_pair_fixed_windows_always_deploy",
        "oracle_status": "failed", "feasibility_label": None,
        "task_exact_communication_treatment_validated": False,
        **CLOSED_VALIDATION_FLAGS,
    }
    return request


def ground_policy_condition_event(request, session, communication, pump_archive):
    """Resolve only this policy's proven ground-only communication domain.

    Scan the actual accepted tasks and robot endpoints, not the policy name or
    a caller's claim. This is not a general cross-mobility event detector.
    """
    from .goal_restaurant_conditions import resolve_restaurant_condition_event
    from .provenance import canonical_sha256

    mobility = {row["id"]: row["mobility"]
                for row in request["public_task"]["candidate_robot_pool"]}
    assigned = {agent for row in session["tasks"].values()
                for agent in row["task"]["assigned_agents"]}
    endpoints = set()
    for message in communication["messages"].values():
        endpoints.add(message["payload"]["producer_id"])
        endpoints.update(message["recipients"])
    robot_endpoints = endpoints & set(mobility)
    if not assigned or any(mobility.get(agent) != "ground"
                           for agent in assigned | robot_endpoints):
        raise ValueError("ground-only condition resolver does not cover this executed policy")
    receipt = pump_archive["readiness"]["readiness_service"]["receipt"]
    event = resolve_restaurant_condition_event(
        request["task_conditions"], cross_mobility_observation=None,
        first_business_commit_at_s=receipt["time_s"] if receipt else None,
        communication_contract=communication["initial_contract"])
    return {"schema": "eai.restaurant.public_policy_condition_review", "version": 1,
            "episode_id": session["episode_id"], "event": event,
            "coverage": "accepted_tasks_and_actual_robot_transport_endpoints_all_ground",
            "assigned_agents": sorted(assigned), "robot_endpoints": sorted(robot_endpoints),
            "session_sha256": canonical_sha256(session),
            "communication_sha256": canonical_sha256(communication),
            "condition_effect_qualified": False, **CLOSED_VALIDATION_FLAGS}


def run_native(args, request):
    from .communication import CommunicationRuntime, validate_communication_report
    from .goal_restaurant_autonomous_v2 import open_restaurant_autonomous_context
    from .goal_restaurant_business_facts import export_restaurant_business_facts
    from .goal_restaurant_deployment_v2 import DEPLOYMENT_PROFILE
    from .goal_restaurant_execution_v2 import RestaurantExecutionPump
    from .goal_restaurant_fleet import RestaurantFleetController
    from .goal_restaurant_native_observation import RestaurantNativeObservationProvider
    from .goal_restaurant_public_policy import PublicRestaurantParticipant
    from .goal_session_audit import replay_goal_session_audit

    os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = request["products_root"]
    from simulator import SimulatorLaunchConfig, open_simulator_session

    def configure(env_cfg):
        env_cfg.episode_length_s = 3600.
        env_cfg.sim.enable_scene_query_support = True

    selection = request["selection"]
    config = SimulatorLaunchConfig(
        env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=args.device, seed=args.seed, headless=True,
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True, env_cfg_hook=configure)
    with open_simulator_session(config) as simulator:
        fleet = RestaurantFleetController(simulator, products_root=args.products_root.resolve(),
                                          deployment_profile=DEPLOYMENT_PROFILE)
        metadata = fleet.begin_episode(args.episode_id, warmup_ticks=args.warmup_ticks)
        _write(args.output, "episode-metadata.json", metadata)
        _write(args.output, "initial.json", fleet.snapshot())
        adapter = PublicRestaurantParticipant()
        runtime = CommunicationRuntime(request["communication_contract"])
        provider = RestaurantNativeObservationProvider(fleet)
        with open_restaurant_autonomous_context(
                fleet, adapter, deployment=request["deployment"]) as context:
            pump = RestaurantExecutionPump(context, runtime=runtime, observation_provider=provider)
            error = None
            try:
                pump.run(max_ticks=args.max_ticks)
            except Exception as exc:
                error = {"type": type(exc).__name__, "error": str(exc),
                         "traceback": traceback.format_exc()}
            # Preserve primary facts before replay/scoring operations which may reject them.
            documents = {
                "context.json": context.archive(), "session.json": context.session.report(),
                "audit.json": context.session.audit_journal(), "communication.json": runtime.report(),
                "final.json": fleet.snapshot(),
                "participant.json": {"plan": adapter.plan, "received_events": adapter.events,
                                     "submitted_actions": adapter.actions, "finished": adapter.finished},
            }
            for name, value in documents.items():
                _write(args.output, name, value)
            if error:
                _write(args.output, "execution-error.json", error)
            archive = pump.report()
            _write(args.output, "pump.json", archive)
            audit = replay_goal_session_audit(documents["audit.json"],
                                               request["public_task"], request["participant_api"])
            communication = validate_communication_report(
                documents["communication.json"],
                expected_initial_contract=request["communication_contract"])
            _write(args.output, "audit-review.json", audit)
            _write(args.output, "communication-review.json", communication)
            drift = [path for path, digest in request["source_sha256"].items()
                     if not Path(path).is_file()
                     or hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest]
            session = documents["session.json"]
            condition_review = ground_policy_condition_event(
                request, session, documents["communication.json"], archive)
            _write(args.output, "condition-review.json", condition_review)
            terminal_statuses = {"succeeded", "failed", "cancelled", "superseded"}
            all_terminal = bool(session["tasks"]) and all(
                row["status"] in terminal_statuses for row in session["tasks"].values())
            report = {
                "status": "execution_archived" if error is None and not drift else "diagnostic_failed",
                "error": error, "source_drift": drift, "time_s": fleet.time_s,
                "task_statuses": {k: v["status"] for k, v in session["tasks"].items()},
                "all_plan_tasks_terminal": all_terminal,
                "bounded_tick_limit_reached": len(archive["ticks"]) == args.max_ticks,
                "trusted_episode_horizon_termination": False,
                "oracle_status": session["deployment_assessment"]["external_evaluation"]["oracle_status"],
                "task_exact_communication_treatment_validated": False,
                "condition_trigger_status": condition_review["event"]["trigger_status"],
                "formal_statistics_eligible": False, **CLOSED_VALIDATION_FLAGS,
            }
            _write(args.output, "report.json", report)
            if error or drift:
                raise RuntimeError("native diagnostic archived an execution or source-drift error")
            inputs = dict(
                public_task=request["public_task"], deployment_companion=request["deployment"],
                episode_metadata=metadata, context_report=documents["context.json"],
                session_report=session, audit_journal=documents["audit.json"], audit_replay=audit,
                pump_report=archive, communication_report=documents["communication.json"],
                communication_replay=communication, archived_source_sha256=request["source_sha256"],
                expected_source_sha256=request["source_sha256"])
            export_restaurant_business_facts(args.output / "scoring", request["public_task"],
                                            episode_id=args.episode_id, verifier_inputs=inputs)
            report.update(status="diagnostic_finished", scoring_exported=True)
            _write(args.output, "report.json", report)
            return report


def main(argv=None):
    args = parser().parse_args(argv)
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite existing archive: {args.output}")
    request = prepare_request(args)
    args.output.mkdir(parents=True, exist_ok=False)
    _write(args.output, "request.json", request)
    try:
        from tools.assets.prepare_restaurant_benchmark_assets import inspect_candidate
        assets = inspect_candidate(args.products_root.resolve())
        _write(args.output, "asset-preflight.json", assets)
        if assets["status"] != "ready":
            raise RuntimeError("frozen Restaurant asset closure is not ready")
        if args.preflight_only:
            result = {"status": "preflight_ready", "native_started": False,
                      "oracle_status": "failed", "feasibility_label": None,
                      **CLOSED_VALIDATION_FLAGS}
            _write(args.output, "preflight-result.json", result)
        else:
            result = run_native(args, request)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        _write(args.output, "failure.json", {
            "status": "diagnostic_runner_error", "type": type(exc).__name__, "error": str(exc),
            "traceback": traceback.format_exc(), "participant_failure_inferred": False,
            **CLOSED_VALIDATION_FLAGS})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
