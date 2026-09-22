"""Four-scene reference conformance probes, separate from business evaluation.

Each probe initializes an explicit symbolic diagnostic state, submits one
navigation action through the real GoalSession, exercises hold/release from a
trusted reference occupancy fixture and archives the backend evidence. It does
not use these positions as the compiled scene spawn poses or consume witnesses.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .goal_deployment import SCENES, validate_goal_deployment
from .goal_driven_contract import GOAL_PLAN_VERSION, validate_goal_plan
from .provenance import canonical_sha256, file_sha256
from .reliable_skill_backend import ReliableSkillReferenceHost

_REFERENCE_SOURCES = (
    "goal_deployment_conformance.py", "reliable_skill_backend.py", "goal_deployment.py",
    "goal_session.py", "goal_driven_contract.py", "goal_constraints.py", "skill_ontology.py", "provenance.py",
)


def run_reference_conformance(deployment):
    sources = {str(Path(__file__).with_name(name).absolute()): file_sha256(Path(__file__).with_name(name))
               for name in _REFERENCE_SOURCES}
    validate_goal_deployment(deployment)
    public = deployment["public_task"]
    world = public["public_world"]
    regions = {r["id"]: r for r in world["regions"]}
    ground = next(r for r in public["candidate_robot_pool"] if "ground_navigation" in r["public_capabilities"])
    task = None
    # Select a legal navigation diagnostic using only public data; no scene
    # feasibility claim is made by selecting it from the abstract route graph.
    for route in world["routes"]:
        if "ground_navigation" not in route["access_modes"]:
            continue
        candidate = {"task_id": "reference_navigation", "skill": "ground_navigation",
                     "arguments": {"region_id": route["to_region_id"], "route_ids": [route["id"]]},
                     "prerequisites": [], "required_capabilities": ["ground_navigation"],
                     "assigned_agents": [ground["id"]], "evidence_refs": [],
                     "execution": {"start_s": 0., "duration_s": public["execution_contract"]["skill_durations_s"]["ground_navigation"]}}
        plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION, "plan_id": "reference_diagnostic",
                "assigned_team": [ground["id"]], "tasks": [candidate]}
        try:
            validate_goal_plan(plan, task_spec=public)
        except ValueError:
            continue
        task = candidate
        break
    if task is None:
        raise ValueError("no publicly legal ground navigation diagnostic")
    robot_regions = {}
    for robot in public["candidate_robot_pool"]:
        mode = "aerial_navigation" if robot["mobility"] == "aerial" else "ground_navigation"
        robot_regions[robot["id"]] = next(r["id"] for r in regions.values() if mode in r["access_modes"])
    robot_regions[ground["id"]] = route["from_region_id"]
    host = ReliableSkillReferenceHost(public, robot_regions=robot_regions)

    def submit(kind, payload):
        ledger = host.report()["ledger"]
        return host.submit_action({"schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": f"reference_{len(ledger['actions'])}", "submitted_at_s": ledger["current_time_s"],
            "type": kind, "payload": payload})

    submit("select_team", {"agent_ids": [ground["id"]]})
    submit("submit_plan", {"plan": plan})
    submit("dispatch_skill", {"task_id": task["task_id"], "evidence_refs": []})
    end = task["execution"]["duration_s"]
    initial = host.state()
    missing = host.execute_pending(task["task_id"], completed_at_s=end)
    host.set_occupancy(time_s=0., blocked_routes={route["id"]: ["reference_idle_blocker"]},
                       blocked_regions={}, evidence_ref="reference:blocked-route")
    blocked = host.execute_pending(task["task_id"], completed_at_s=end)
    if missing["status"] != "pending" or blocked["status"] != "held" or host.state() != initial:
        raise ValueError("reference occupancy preconditions changed state or failed to hold")
    host.set_occupancy(time_s=0., blocked_routes={}, blocked_regions={},
                       evidence_ref="reference:clear-route-not-a-scene-measurement")
    completion = host.execute_pending(task["task_id"], completed_at_s=end)
    if not completion["accepted"] or host.state()["robot_regions"][ground["id"]] != route["to_region_id"]:
        raise ValueError("reference navigation did not apply its effect")
    result = {"schema": "eai.benchmark.deployment_reference_conformance", "version": 1,
              "visibility": "author_only", "scene_key": deployment["scene_key"],
              "deployment_sha256": deployment["sha256"],
              "origin": "deterministic_reference_not_simulator",
              "initial_state_basis": "explicit_public_graph_diagnostic_not_scene_spawn_binding",
              "plan": plan, "initial_state": initial, "missing_occupancy": missing,
              "blocked_occupancy": blocked, "completion": completion, "backend_report": host.report(),
              "reference_conformance_validated": True, "physical_execution_validated": False,
              "business_outcomes_validated": False, "scoreable": False,
              "source_files": sources}
    if any(file_sha256(Path(path)) != digest for path, digest in sources.items()):
        raise ValueError("reference implementation changed during execution")
    return {**result, "sha256": canonical_sha256(result)}


def verify_reference_conformance(report, deployment):
    expected = run_reference_conformance(deployment)
    if canonical_sha256(report) != canonical_sha256(expected):
        raise ValueError("reference conformance differs from current deterministic execution")
    return {"validated": True, "scene_key": report["scene_key"], "scoreable": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    reports = []
    for scene in SCENES:
        deployment = json.loads((args.deployment_dir / f"{scene}.json").read_text())
        reports.append(run_reference_conformance(deployment))
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for report in reports:
        with (args.output_dir / f"{report['scene_key']}.json").open("x") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
    print(json.dumps({"scene_count": len(reports), "reference_conformance_validated": True,
                      "physical_execution_validated": False, "scoreable": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
