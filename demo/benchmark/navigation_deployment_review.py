"""Read-only acceptance of the existing navigation-smoke runtime report.

``audit_navigation_deployment(report_path, *, scenario_path, expected_scene)``
reads the final JSON and its sibling ``.request.json`` (and, when present,
``.runtime.json`` / ``.failure.json``). It never launches a simulator, imports
the runtime, changes evidence, or grants business/whole-task scoring authority.
Missing historical evidence is pending; contradictory bindings raise ValueError.

An optional ``control_step_evidence`` binds commands submitted to env.step to
the immediate post-step root-state readback and the sampled trajectory. Legacy
reports without it retain pending command evidence. Submitted commands are not
actuator-application receipts, so an accepted navigation observation does NOT
certify physical controller success. File
hashes detect identity changes, not forgery; this is a local evidence review,
not independent process attestation. Explicit synthetic or pose-override reports
cannot be admitted as physical navigation evidence.

Captured sources are checked against their current files. New, unexecuted files
may have been added after the run (including this auditor); they are listed,
never retroactively included in the execution provenance. Changed/deleted
captured sources or assets are rejected. The report file's mtime supplies only
the upper timestamp bound needed by the existing provenance validator, not a
fabricated runtime completion timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from demo.benchmark.control_step_evidence import (
    COMMAND_SCOPE, validate_control_step_evidence,
)
from demo.benchmark.provenance import (
    PROVENANCE_SCHEMA, RUNTIME_DEFAULTS, canonical_sha256,
    execution_code_provenance, file_sha256, validate_execution_provenance,
)

SCHEMA = "eai.benchmark.navigation_deployment_review"
_FAILURES = {"steps_exhausted", "deadlocked", "physics_state_invalid",
             "unsupported_runtime_lifecycle_event", "runtime_exception"}
_TIMEOUTS = {"timed_out", "wall_time_exhausted"}


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _finite(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _step(value):
    return type(value) is int and value >= 0


def _vector(value, length):
    return isinstance(value, list) and len(value) == length and all(map(_finite, value))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def _read(path, bindings, label, *, optional=False):
    if optional and not path.exists() and not path.is_symlink():
        return None
    raw = path.read_bytes()
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_bad_constant)
    _object(value, label)
    # Also rejects numbers such as 1e999, which json.loads converts to infinity.
    canonical_sha256(value)
    bindings[label] = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                       "canonical_sha256": canonical_sha256(value)}
    return value


def _snapshot(snapshot, label):
    _object(snapshot, label)
    content = {k: v for k, v in snapshot.items() if k not in {"sha256", "captured_at"}}
    if (snapshot.get("schema") != PROVENANCE_SCHEMA
            or snapshot.get("sha256") != canonical_sha256(content)):
        raise ValueError(f"{label} provenance snapshot digest/schema mismatch")
    try:
        timestamp = datetime.fromisoformat(snapshot["captured_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise ValueError(f"{label} provenance timestamp missing or invalid") from error
    if timestamp.tzinfo is None or timestamp > datetime.now(timezone.utc):
        raise ValueError(f"{label} provenance timestamp is naive or in the future")


def _request(request, scenario_hash):
    _snapshot(request, "request")
    if request.get("scenario_sha256") != scenario_hash:
        raise ValueError("request/scenario disk hash mismatch")
    options = _object(request.get("options"), "request.options")
    if set(options) != set(RUNTIME_DEFAULTS):
        raise ValueError("request has incomplete runtime options")
    captured = _object(request.get("code_provenance"), "request.code_provenance")
    required = {"demo/benchmark/runtime.py", "demo/benchmark/provenance.py", "simulator.py"}
    if not required <= captured.keys():
        raise ValueError("request has incomplete runtime source provenance")
    current = execution_code_provenance()
    mismatches = sorted(key for key, digest in captured.items() if current.get(key) != digest)
    if mismatches:
        raise ValueError(f"captured source hash mismatch: {mismatches}")
    return sorted(current.keys() - captured.keys())


def _provenance(report, request, runtime_sidecar, bindings, report_path):
    proof = report.get("execution_provenance")
    pending = []
    added_sources = []
    if request is not None:
        added_sources = _request(request, bindings["scenario"]["sha256"])
    else:
        pending.append("missing_disk_request")
    if runtime_sidecar is not None:
        _snapshot(runtime_sidecar, "runtime sidecar")
    if proof is None:
        pending.append("missing_execution_provenance")
        return {"status": "pending", "validated": False, "pending": pending,
                "additional_current_sources": added_sources}
    _object(proof, "execution_provenance")
    if (proof.get("schema") != PROVENANCE_SCHEMA or proof.get("validated") is not True
            or proof.get("violations") != []):
        raise ValueError("execution provenance is incomplete or has recorded violations")
    embedded = proof.get("request")
    _snapshot(embedded, "embedded request")
    if embedded.get("scenario_sha256") != bindings["scenario"]["sha256"]:
        raise ValueError("report/scenario disk hash mismatch")
    if request is not None and embedded != request:
        raise ValueError("report/request disk binding mismatch")
    runtime = proof.get("runtime")
    _snapshot(runtime, "runtime")
    if runtime_sidecar is not None and runtime_sidecar != runtime:
        raise ValueError("report/runtime sidecar mismatch")
    if pending:
        # Never reconstruct a purported pre-launch disk request from the report.
        return {"status": "pending", "validated": False, "pending": pending,
                "additional_current_sources": added_sources}
    options = request["options"]
    attempt = {
        "report_body": report, "report_sha256": canonical_sha256(report),
        "scenario_sha256": bindings["scenario"]["sha256"],
        "attempted_at": datetime.fromtimestamp(report_path.stat().st_mtime, timezone.utc).isoformat(),
        "config": {"request": request, "code_provenance": request["code_provenance"],
                   "transport_mode": options["transport_mode"],
                   "steps": options["steps"] or "auto", "device": options["device"]},
    }
    validate_execution_provenance(
        attempt, expected_sources=request["code_provenance"],
        expected_options=options, check_runtime_files=True)
    return {"status": "verified", "validated": True, "pending": [],
            "request_snapshot_sha256": request["sha256"],
            "runtime_snapshot_sha256": runtime["sha256"],
            "additional_current_sources": added_sources,
            "timestamp_upper_bound_source": "report_file_mtime"}


def _navigation_spec(scenario, expected_scene):
    if not isinstance(expected_scene, str) or not expected_scene:
        raise ValueError("expected_scene must be a nonempty scene key")
    if scenario.get("scene_key") != expected_scene:
        raise ValueError("scenario/expected_scene mismatch")
    robots, targets, nodes = {}, {}, {}
    for name, values, output in (
        ("robots", scenario.get("robots"), robots),
        ("targets", _object(scenario.get("composition"), "composition").get("targets"), targets),
        ("nodes", _object(scenario.get("phase_graph"), "phase_graph").get("nodes"), nodes),
    ):
        if not isinstance(values, list):
            raise ValueError(f"scenario {name} must be a list")
        for value in values:
            identifier = _object(value, name).get("id")
            if not isinstance(identifier, str) or not identifier or identifier in output:
                raise ValueError(f"scenario {name} require unique nonempty ids")
            output[identifier] = value
    supported = bool(nodes) and all(n.get("skill_key") == "navigate" for n in nodes.values())
    for node in nodes.values():
        if node.get("skill_key") != "navigate":
            continue
        if node.get("preferred_agent") not in robots or node.get("target_ref") not in targets:
            raise ValueError("navigation node references an unknown robot/target")
        target = targets[node["target_ref"]]
        if (not _vector(target.get("position"), 3) or not _finite(target.get("radius_m"))
                or target["radius_m"] <= 0):
            raise ValueError("navigation target requires finite position and positive radius")
    return robots, targets, nodes, supported


def _nonphysical(report, scenario, request):
    reasons = []
    # These explicit declarations can only restrict admission; they never add
    # authority. Do not scan free-form limitations (which mention kinematics).
    for source in (report, report.get("execution_validation") or {}):
        _object(source, "execution_validation")
        if any(source.get(key) is True for key in ("synthetic", "fixture", "pure_fixture", "pose_override")):
            reasons.append("explicit_fixture_or_pose_override")
        if source.get("evidence_origin") in {"fixture", "pure_fixture", "synthetic"}:
            reasons.append("explicit_synthetic_evidence")
        if source.get("tier") in {"pure_fixture", "kinematic_execution", "kinematic_execution_validated"}:
            reasons.append("nonphysical_execution_tier")
        if source.get("transport_mode") == "kinematic":
            reasons.append("kinematic_transport")
        if source.get("aerial_motion_mode") == "kinematic":
            reasons.append("kinematic_aerial_motion")
    # Warehouse's unused aerial option defaults to kinematic even for Carter.
    # It must not reclassify a ground-only smoke as a pose-override run.
    aerial = any(robot.get("type") in {"iris", "crazyflie"} for robot in scenario["robots"])
    if aerial and request and request["options"].get("aerial_motion_mode") == "kinematic":
        reasons.append("kinematic_aerial_motion")
    for source in _object(report.get("navigation_state_sources", {}), "navigation_state_sources").values():
        if source in {"kinematic", "scripted_root_pose", "pose_override"}:
            reasons.append("pose_override_readback")
    return sorted(set(reasons))


def _poses(report, robots):
    pending = []
    trajectory = report.get("pose_trajectory")
    if trajectory is None:
        return {"status": "pending", "pending": ["missing_pose_trajectory"]}, {}
    _object(trajectory, "pose_trajectory")
    if trajectory.get("frame") != "world" or trajectory.get("position_unit") != "m":
        raise ValueError("pose trajectory must use world frame and metres")
    samples = trajectory.get("samples")
    if not isinstance(samples, list):
        raise ValueError("pose trajectory samples must be a list")
    steps = report.get("steps_taken")
    by_step = {}
    last_step = -1
    for sample in samples:
        _object(sample, "pose sample")
        step = sample.get("step")
        if not _step(step) or step < last_step or (_step(steps) and step > steps):
            raise ValueError("pose step order/budget mismatch")
        positions = _object(sample.get("positions"), "sample.positions")
        poses = sample.get("robot_poses_xyzyaw")
        if poses is None:
            pending.append("missing_pose_xyzyaw_readback")
        else:
            _object(poses, "robot_poses_xyzyaw")
        for agent in robots:
            xy = positions.get(agent)
            if xy is None:
                pending.append(f"missing_position:{agent}")
                continue
            if not _vector(xy, 2):
                raise ValueError("sample position must be finite XY")
            if poses is not None:
                pose = poses.get(agent)
                if pose is None:
                    pending.append(f"missing_pose:{agent}")
                elif not _vector(pose, 4) or math.dist(pose[:2], xy) > 1e-6:
                    raise ValueError("pose/position readback mismatch")
        if step in by_step and by_step[step] != sample:
            raise ValueError("conflicting pose samples at the same step")
        by_step[step] = sample
        last_step = step
    if not samples or 0 not in by_step or not _step(steps) or steps not in by_step or steps == 0:
        pending.append("missing_initial_or_post_step_final_sample")
    sources = _object(report.get("navigation_state_sources", {}), "navigation_state_sources")
    for agent in robots:
        if sources.get(agent) not in {"articulation_root", "body:base_link"}:
            pending.append(f"unverified_navigation_state_source:{agent}")
    final = _object(report.get("final_poses", {}), "final_poses")
    if samples:
        for agent in robots:
            pose = final.get(agent)
            measured = (samples[-1].get("robot_poses_xyzyaw") or {}).get(agent)
            if pose is None:
                pending.append(f"missing_final_pose:{agent}")
            elif not _vector(pose, 3):
                raise ValueError("final pose must be finite XYZ")
            elif measured is not None and any(abs(a - b) > .005001 for a, b in zip(pose, measured)):
                # runtime.json_safe_finite_values rounds the summary to 2 decimals.
                raise ValueError("final pose/trajectory mismatch")
    displacements = {}
    if 0 in by_step and steps in by_step:
        for agent in robots:
            first = by_step[0]["positions"].get(agent)
            last = by_step[steps]["positions"].get(agent)
            if first is not None and last is not None:
                displacements[agent] = math.dist(first, last)
    return {"status": "observed" if not pending else "pending", "sample_count": len(samples),
            "sources": {agent: sources.get(agent) for agent in robots},
            "displacement_xy_m": displacements, "pending": sorted(set(pending))}, by_step


def _loop(report, provenance, poses, request):
    pending = []
    steps, dt = report.get("steps_taken"), report.get("control_dt_s")
    if steps is not None and not _step(steps):
        raise ValueError("steps_taken must be a nonnegative integer")
    if dt is not None and (not _finite(dt) or dt <= 0):
        raise ValueError("control_dt_s must be finite and positive")
    if not steps or dt is None:
        pending.append("missing_positive_control_loop")
    budget = request["options"]["steps"] if request else None
    if budget is not None and (not _step(budget) or (budget and _step(steps) and steps > budget)):
        raise ValueError("control loop/request step budget mismatch")
    scheduling = _object(report.get("scheduling", {}), "scheduling")
    end = scheduling.get("time_s")
    if end is None:
        pending.append("missing_scheduler_end_time")
    elif not _finite(end) or end < 0 or (
            _step(steps) and dt is not None and not math.isclose(end, steps * dt, abs_tol=1e-6)):
        raise ValueError("scheduler/control loop end time mismatch")
    runtime = (report.get("execution_provenance") or {}).get("runtime") or {}
    versions = (runtime.get("effective") or {}).get("versions") or {}
    if any(not isinstance(versions.get(name), str) or versions[name] in {"", "unknown"}
           for name in ("isaacsim", "isaaclab")):
        pending.append("missing_isaac_runtime_versions")
    files = runtime.get("files") or {}
    from demo.benchmark.provenance import REPO_ROOT
    if str(REPO_ROOT / "demo/benchmark/runtime.py") not in files:
        pending.append("missing_loaded_runtime_source")
    if not provenance["validated"]:
        pending.append("unverified_execution_provenance")
    if poses["status"] != "observed":
        pending.append("incomplete_pose_readback")
    return {"status": "observed" if not pending else "pending", "steps_taken": steps,
            "simulation_time_s": steps * dt if _step(steps) and dt is not None else None,
            "versions": versions, "pending": pending,
            "basis": "source_bound_runtime_report_and_post_step_pose_samples"}


def _goals(report, nodes, targets, samples):
    results = []
    events = report.get("events", [])
    if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
        raise ValueError("events must be a list of objects")
    states = _object(_object(report.get("scheduling", {}), "scheduling").get("states", {}), "states")
    for identifier, node in nodes.items():
        if node.get("skill_key") != "navigate":
            continue
        agent, target_ref = node["preferred_agent"], node["target_ref"]
        target = targets[target_ref]
        candidates = [e for e in events if e.get("event") == "node_complete" and e.get("node") == identifier]
        state = _object(states.get(identifier, {}), "node state")
        result = {"node": identifier, "agent": agent, "target_ref": target_ref,
                  "radius_m": target["radius_m"], "status": "pending"}
        if not candidates:
            result["reason"] = "missing_measured_navigation_arrival"
        elif len(candidates) != 1:
            raise ValueError("duplicate navigation completion events")
        else:
            event = candidates[0]
            if event.get("agent") != agent:
                raise ValueError("arrival agent does not match scenario assignment")
            expected_predicate = {"fn": "robot_visited_zone", "args": [agent, target_ref]}
            if (event.get("confirmed_after_sim_step") is not True
                    or event.get("arrival_predicate") is None):
                result["reason"] = "missing_post_step_arrival_evidence"
            elif event["arrival_predicate"] != expected_predicate:
                raise ValueError("arrival predicate does not match scenario target")
            elif not _step(event.get("step")) or event["step"] <= 0:
                result["reason"] = "arrival_not_after_positive_simulation_step"
            else:
                sample = samples.get(event["step"])
                xy = event.get("position")
                if not _vector(xy, 2):
                    raise ValueError("arrival position must be finite XY")
                measured = sample["positions"].get(agent) if sample else None
                if measured is None:
                    result["reason"] = "missing_arrival_pose_sample"
                elif math.dist(xy, measured) > 1e-6:
                    raise ValueError("arrival event/pose sample mismatch")
                else:
                    error = math.dist(xy, target["position"][:2])
                    result.update(arrival_step=event["step"], arrival_error_m=error)
                    if error > target["radius_m"]:
                        raise ValueError("claimed navigation arrival lies outside target")
                    if state.get("status") != "succeeded":
                        result["reason"] = "missing_succeeded_navigation_state"
                    else:
                        finished, dt = state.get("finished_at_s"), report.get("control_dt_s")
                        if not _finite(finished) or not _finite(dt):
                            result["reason"] = "missing_navigation_finish_time"
                        elif not math.isclose(finished, event["step"] * dt, abs_tol=1e-6):
                            raise ValueError("navigation finish time/arrival step mismatch")
                        else:
                            ready = state.get("ready_at_s")
                            timeout = node.get("timeout_s")
                            if ready is None:
                                result["reason"] = "missing_navigation_ready_time"
                            elif (not _finite(ready) or not _finite(timeout) or timeout <= 0
                                  or ready < 0 or ready > finished or finished - ready >= timeout):
                                raise ValueError("navigation arrival/deadline mismatch")
                            else:
                                result.update(status="observed", arrival_time_s=finished)
        results.append(result)
    return {"status": "observed" if results and all(r["status"] == "observed" for r in results)
            else "pending", "nodes": results, "predicate_scope": "robot_visited_zone_xy"}


def _controller_commands(report, robots, samples, *, provenance_validated):
    result = {
        "status": "pending", "reason": "missing_control_step_evidence",
        "configured_controllers": {k: v.get("controller") for k, v in robots.items()},
        "command_scope": COMMAND_SCOPE, "actuator_applied_validated": False,
        "physical_control_validated": False,
    }
    if "control_step_evidence" not in report:
        return result
    evidence = validate_control_step_evidence(
        report["control_step_evidence"], steps_taken=report.get("steps_taken"),
        control_dt_s=report.get("control_dt_s"))
    if not robots.keys() <= set(evidence["robot_ids"]):
        raise ValueError("control step evidence misses scenario command robots")
    sources = _object(report.get("navigation_state_sources", {}), "navigation_state_sources")
    if not set(evidence["state_ids"]) <= sources.keys():
        raise ValueError("control step evidence contains a foreign articulation inventory")
    pending = []
    from demo.benchmark.provenance import REPO_ROOT
    collector_source = "demo/benchmark/control_step_evidence.py"
    proof = report.get("execution_provenance") or {}
    captured_sources = (proof.get("request") or {}).get("code_provenance") or {}
    runtime_files = (proof.get("runtime") or {}).get("files") or {}
    collector_digest = captured_sources.get(collector_source)
    if (not collector_digest
            or runtime_files.get(str(REPO_ROOT / collector_source)) != collector_digest):
        pending.append("collector_not_bound_to_loaded_runtime_sources")
    comparisons = 0
    for step, sample in samples.items():
        if step == 0:
            continue  # No env.step was submitted for the initial reset sample.
        states = evidence["samples"][step - 1]["post_root_states"]
        poses = _object(sample.get("robot_poses_xyzyaw", {}), "robot_poses_xyzyaw")
        # Include extra observed articulations when the recorder captured them.
        for robot in sorted(set(evidence["robot_ids"]) | (poses.keys() & states.keys())):
            if sources.get(robot) != "articulation_root":
                pending.append(f"root_state_cannot_bind_navigation_body:{robot}")
                continue
            pose = poses.get(robot)
            xy = sample["positions"].get(robot)
            if pose is None or xy is None:
                pending.append(f"missing_sampled_root_pose:{robot}")
                continue
            if not _vector(pose, 4) or not _vector(xy, 2):
                raise ValueError("control step comparison requires finite XY/XYZyaw")
            state = states[robot]
            if any(abs(a - b) > 1e-6 for a, b in zip(state[:3], pose[:3])) or math.dist(state[:2], xy) > 1e-6:
                raise ValueError("control step root state/pose sample mismatch")
            w, x, y, z = state[3:7]
            norm = math.hypot(w, x, y, z)
            if not math.isclose(norm, 1., rel_tol=0., abs_tol=1e-3):
                raise ValueError("control step root quaternion is not a rotation")
            yaw = math.atan2(2. * (w * z + x * y), 1. - 2. * (y * y + z * z))
            if abs(math.remainder(yaw - pose[3], 2. * math.pi)) > 1e-6:
                raise ValueError("control step root yaw/pose sample mismatch")
            comparisons += 1
    if not comparisons or report["steps_taken"] not in samples:
        pending.append("missing_post_step_pose_comparison")
    if not provenance_validated:
        pending.append("unverified_execution_provenance")
    result.update(evidence_sha256=evidence["sha256"], sample_count=evidence["sample_count"],
                  robot_ids=evidence["robot_ids"], compared_pose_count=comparisons,
                  pending=sorted(set(pending)))
    if pending:
        result["reason"] = "control_step_evidence_not_fully_bound"
    else:
        result.update(status="submitted_and_post_step_readback", reason=None)
    return result


def audit_navigation_deployment(report_path, *, scenario_path, expected_scene):
    """Return JSON-safe read-only evidence review; reject mismatched evidence.

    ``navigation_validated`` concerns only the reported navigation diagnostic.
    ``physical_control_validated``, ``business_task_validated``,
    ``execution_validated`` (whole task), and ``scoreable`` always remain False.
    No final file means ``no_summary``; an outer process timeout cannot be
    inferred from that absence. Preserve a custom --execution-request beside
    the report as .request.json to provide the required disk request binding.
    """
    report_path = Path(report_path).absolute()
    scenario_path = Path(scenario_path).absolute()
    bindings = {}
    scenario = _read(scenario_path, bindings, "scenario")
    robots, targets, nodes, supported = _navigation_spec(scenario, expected_scene)
    request = _read(report_path.with_suffix(".request.json"), bindings, "request", optional=True)
    report = _read(report_path, bindings, "report", optional=True)
    runtime = _read(report_path.with_suffix(".runtime.json"), bindings, "runtime", optional=True)
    failure = _read(report_path.with_suffix(".failure.json"), bindings, "failure", optional=True)
    result = {
        "schema": SCHEMA, "version": 1, "scope": "navigation_deployment_only",
        "scene_key": expected_scene, "task_name": scenario.get("task_name"), "bindings": bindings,
        "navigation_validated": False, "physical_control_validated": False,
        "business_task_validated": False, "execution_validated": False,
        "whole_task_execution_validated": False, "scoreable": False,
        "controller_commands": _controller_commands({}, robots, {}, provenance_validated=False),
        "limitations": ["File identity is not independent process attestation",
                        "No applied-command receipt or actuator/contact/ground-support certification",
                        "A navigation visit does not validate business outcomes, whole tasks or scores"],
    }
    result["provenance"] = _provenance(report or {}, request, runtime, bindings, report_path)
    if report is None:
        result.update(status="no_summary", reported_status=None,
                      pending=["missing_final_report", "outer_process_outcome_unknown"],
                      failure_sidecar_status=failure.get("status") if failure else None,
                      isaac_loop={"status": "pending"}, pose_readback={"status": "pending"},
                      goal_reached={"status": "pending", "nodes": []})
    else:
        for key in ("scene_key", "task_name"):
            if key in report and report[key] != scenario.get(key):
                raise ValueError(f"report/scenario {key} mismatch")
        poses, samples = _poses(report, robots)
        loop = _loop(report, result["provenance"], poses, request)
        goals = _goals(report, nodes, targets, samples)
        commands = _controller_commands(
            report, robots, samples, provenance_validated=result["provenance"]["validated"])
        result["controller_commands"] = commands
        exclusions = _nonphysical(report, scenario, request)
        reported_status = report.get("status")
        if reported_status is not None and not isinstance(reported_status, str):
            raise ValueError("report status must be a string")
        health = _object(report.get("physics_state_health", {}), "physics_state_health")
        status = "pending"
        if reported_status in _TIMEOUTS:
            status = "timed_out"
        elif (reported_status in _FAILURES or failure is not None
              or health.get("validated") is False or health.get("failure") is not None):
            status = "failed"
        elif reported_status != "executed":
            status = "unknown"
        elif exclusions:
            status = "non_physical"
        elif (supported and result["provenance"]["validated"]
              and ("control_step_evidence" not in report
                   or commands["status"] == "submitted_and_post_step_readback")
              and loop["status"] == poses["status"] == goals["status"] == "observed"):
            validation = _object(report.get("execution_validation", {}), "execution_validation")
            if validation.get("validated") is True and validation.get("tier") == "navigation_execution_validated":
                status = "navigation_observed"
        result.update(status=status, reported_status=reported_status, isaac_loop=loop,
                      pose_readback=poses, goal_reached=goals, exclusions=exclusions,
                      navigation_only_scenario=supported,
                      navigation_validated=status == "navigation_observed")
    # Bind to the bytes actually examined, and reject a writer racing the review.
    for binding in bindings.values():
        if file_sha256(binding["path"]) != binding["sha256"]:
            raise ValueError("evidence file changed during read-only review")
    result["review_sha256"] = canonical_sha256(result)
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_path")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--expected-scene", required=True)
    args = parser.parse_args()
    print(json.dumps(audit_navigation_deployment(
        args.report_path, scenario_path=args.scenario, expected_scene=args.expected_scene),
        ensure_ascii=False, indent=2, allow_nan=False))
