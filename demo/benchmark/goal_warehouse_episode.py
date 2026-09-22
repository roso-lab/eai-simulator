"""Participant-driven native Warehouse overload slice, pending full task binding.

This exercises a real Isaac attachment rejection through FormalGoalEpisodeRunner.
The probe uses an experimental M20 spawn and also runs a carton diagnostic; it
does not establish an order outcome, formal route, or scoreable execution.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .goal_attachment_probe import MEMBER, _validate_formal_binding, execute_probe, prepare_probe
from .goal_driven_contract import GOAL_PLAN_VERSION
from .goal_driven_tasks import build_goal_task_package
from .goal_episode_runner import FormalGoalEpisodeRunner
from .provenance import canonical_sha256, file_sha256


def _action(identifier: str, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": identifier, "submitted_at_s": 0.0,
            "type": kind, "payload": deepcopy(dict(payload))}


class OverloadDiagnosticParticipant:
    """Fixed high-level participant used solely to test the native bridge."""

    def __init__(self):
        self._sent = False

    def manifest(self) -> Mapping[str, Any]:
        return {"kind": "participant", "id": "warehouse-overload-diagnostic", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
                "evaluation_track": "goal_driven", "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task: Mapping[str, Any], participant_api: Mapping[str, Any]) -> None:
        del public_task, participant_api

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._sent or not any(row["type"] == "episode_started" for row in events):
            return []
        self._sent = True
        plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
                "plan_id": "participant_overload", "assigned_team": [MEMBER], "tasks": [{
                    "task_id": "participant_overload_crate", "skill": "pick",
                    "arguments": {"object_id": "order_crate_01", "pickup_region_id": "rack_aisle_a",
                                  "handling_mode_id": "single_grip", "carrier_bindings": [
                                      {"agent_id": MEMBER, "point_id": "center_grip"}]},
                    "required_capabilities": ["pick"], "assigned_agents": [MEMBER],
                    "prerequisites": [], "evidence_refs": [],
                    "execution": {"start_s": 0.0, "duration_s": 4.0},
                }]}
        return [_action("participant_select", "select_team", {"agent_ids": [MEMBER]}),
                _action("participant_plan", "submit_plan", {"plan": plan}),
                _action("participant_dispatch", "dispatch_skill", {
                    "task_id": plan["tasks"][0]["task_id"], "evidence_refs": []})]

    def close(self) -> None:
        pass


class WarehouseNativeOverloadBackend:
    """Verify a fresh native diagnostic before committing its failed receipt."""

    def __init__(self, public_task: Mapping[str, Any], root: Path, *, device: str = "cpu"):
        self.public_task = deepcopy(dict(public_task))
        self.root = Path(root)
        self.device = device

    def execute(self, task: Mapping[str, Any], *, episode_id: str,
                trusted_report: Mapping[str, Any]) -> Mapping[str, Any]:
        ledger = trusted_report["session"]
        task_id = task["task_id"]
        row = ledger["tasks"][task_id]
        if (ledger["episode_id"] != episode_id or row["status"] != "pending"
                or row["task"] != task or row["plan_id"] != ledger["current_plan_id"]):
            raise ValueError("native task does not match pending GoalSession dispatch")
        plan = next(item["plan"] for item in ledger["plans"]
                    if item["plan"]["plan_id"] == row["plan_id"])
        binding = {"episode_id": episode_id, "plan": plan,
                   "dispatch_action_id": row["dispatch_action_id"]}
        _validate_formal_binding(binding, self.public_task)
        root = self.root / "native_attachment"
        request = prepare_probe(root, device=self.device, formal_binding=binding)
        deployment = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
        if canonical_sha256(deployment["public_task"]) != canonical_sha256(self.public_task):
            raise ValueError("native deployment is not the runner's public task")
        process = execute_probe(root)
        if not process["diagnostic_completed"] or process["returncode"] != 0:
            raise RuntimeError("native attachment diagnostic did not complete")
        report = json.loads((root / "attachment_report.json").read_text(encoding="utf-8"))
        formal = json.loads((root / "formal_goal_ledger_after.json").read_text(encoding="utf-8"))
        evidence = formal["evidence"]
        receipt = formal["receipt"]
        evidence_ref = "sha256:" + canonical_sha256(evidence)
        crate_path = report["body_bindings"]["objects"]["order_crate_01"]["body_path"]
        settled = [sample for sample in report["samples"] if sample["phase"] == "settle"]
        later = [sample for sample in report["samples"] if sample["phase"] != "settle"]
        if (not settled or not later or evidence["before"]["body_path"] != crate_path
                or settled[-1]["bodies"][crate_path] != evidence["before"]
                or evidence["after"] != evidence["before"]):
            raise ValueError("native failure evidence lacks a same-step crate readback")
        start = evidence["before"]["position_m"]
        for sample in [*later, report["final_native_snapshot"]]:
            pose = sample["bodies"][crate_path]
            if (pose["body_path"] != crate_path
                    or math.dist(start, pose["position_m"]) > 0.01
                    or pose["position_m"][2] > start[2] + 0.005):
                raise ValueError("native crate moved or lifted after the failed attachment")
        if (report["run_id"] != request["run_id"]
                or process["attachment_report_sha256"] != file_sha256(root / "attachment_report.json")
                or report["formal_goal_episode"]["receipt_ref"] != evidence_ref
                or formal["episode_id"] != episode_id
                or formal["ledger"]["episode_id"] != episode_id
                or formal["ledger"]["tasks"][task_id]["status"] != "failed"
                or formal["ledger"]["tasks"][task_id]["receipt"] != receipt
                or not formal["completion"]["accepted"]
                or evidence["episode_id"] != episode_id or evidence["task_id"] != task_id
                or evidence["object_id"] != "order_crate_01"
                or evidence["object_mass_kg"] != next(
                    obj["physical_properties"]["mass_kg"]
                    for obj in self.public_task["public_world"]["objects"]
                    if obj["id"] == "order_crate_01")
                or evidence["failure_reason"] != "member allocated load exceeds public payload"
                or evidence["source"] != "native_physx_attachment_admission"
                or evidence["object_moved"] is not False or evidence["task_advanced"] is not False
                or evidence["zero_stage_effect"] is not True or evidence["zero_pose_effect"] is not True
                or evidence["before"] != evidence["after"]
                or receipt["episode_id"] != episode_id or receipt["task_id"] != task_id
                or receipt["action_id"] != row["dispatch_action_id"]
                or receipt["receipt_ref"] != evidence_ref or receipt["evidence_refs"] != [evidence_ref]
                or receipt["status"] != "failed"):
            raise ValueError("native failure evidence does not match the runner dispatch")
        return {"status": "failed", "completed_at_s": receipt["completed_at_s"],
                "receipt": receipt,
                "public_observation": {
                    "skill_succeeded": False, "object_moved": False,
                    "task_advanced": False,
                    "carrier_load_monitor": [{
                        "agent_id": task["assigned_agents"][0],
                        "requested_load_kg": evidence["object_mass_kg"],
                        "effective_payload_kg": next(
                            robot["physical_profile"]["effective_payload_kg"]
                            for robot in self.public_task["candidate_robot_pool"]
                            if robot["id"] == task["assigned_agents"][0]),
                    }],
                }}


def run_overload_slice(output_root: str | Path, *, device: str = "cpu") -> dict[str, Any]:
    bundle = build_goal_task_package("warehouse_nvd")
    public = bundle["public_task"]
    regions = {row["id"]: ("rack_aisle_a" if row["mobility"] == "ground" else "rack_overview")
               for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}
    root = Path(output_root).absolute()
    runner = FormalGoalEpisodeRunner(public, bundle["participant_api"],
        OverloadDiagnosticParticipant(), WarehouseNativeOverloadBackend(public, root, device=device),
        output_root=root, max_turns=8)
    return runner.run(initial_state={"robot_regions": regions, "object_states": objects})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    args = parser.parse_args(argv)
    result = run_overload_slice(args.output_root, device=args.device)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["error"] is None and result["skill_receipt_count"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
