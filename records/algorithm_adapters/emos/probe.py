"""Reproducible native EMOS IO probe; neither mode is algorithm acceptance.

Protocol mode uses explicitly scripted completions. Live mode uses only the
existing EMOS/OpenAI environment and exits 2 if absent. Both use synthetic input.
No upstream prompts or decisions are patched. Run from the repository root.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from algorithm.common_coordination.contract import CoordinationTask, RobotCapability, TaskGraph
from algorithm.system.coordination.emos.native_adapter import (
    EmosNativeAdapter, MeteredNativeEndpoint, NativeEmosError, NativeEmosUnavailable,
)
from benchmark.emos import CaseAssignmentRequest
from task.contracts import EaiTaskPackage, RobotState


class ProtocolTransport:
    """Canned IO responses, deliberately not an algorithm implementation."""
    def __init__(self):
        self.answers = iter([
            "{r1||Inspect object one}{r2||Inspect object two}",
            "{no||Protocol fixture rejection exercises native reassignment}", "{yes}",
            "{r1||Inspect object two}{r2||Inspect object one}", "{yes}", "{yes}",
        ])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        from openai.types.chat import ChatCompletion
        return ChatCompletion.model_validate({
            "id": "explicit-protocol-double", "object": "chat.completion", "created": 0,
            "model": kwargs["model"], "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": next(self.answers), "tool_calls": None}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("protocol", "live"), required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    receipt = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "mode": args.mode,
               "input_kind": "synthetic_protocol_fixture", "algorithm_acceptance": "not_passed",
               "physical_acceptance": "not_evaluated", "fallback_used": False,
               "source_root": str(args.source_root.resolve()), "model_requests": 0}
    endpoint = None
    adapter = None
    exit_code = 2
    try:
        endpoint = (MeteredNativeEndpoint.from_environment(max_calls=16, timeout=30)
                    if args.mode == "live" else
                    MeteredNativeEndpoint(ProtocolTransport(), model="explicit-protocol-double", max_calls=16))
        request = CaseAssignmentRequest("Inspect object one and object two.", "emos-native-probe",
                                        {"r1": (0, 0, 0), "r2": (1, 0, 0)}, ("t1", "t2"))
        robots = tuple(RobotState(RobotCapability(name, frozenset({"inspect"})), pos)
                       for name, pos in request.robot_positions.items())
        package = EaiTaskPackage(request.scenario_id, "synthetic-protocol-scene", TaskGraph((
            CoordinationTask("t1", "Inspect object one"), CoordinationTask("t2", "Inspect object two")),
            source="synthetic-protocol-fixture"), robots)
        resumes = {name: {"robot_type": "ProtocolFixtureRobot",
                         "mobility": {"summary": "Synthetic robot moves on a flat plane"},
                         "perception": {"summary": "Synthetic camera can inspect either object"}}
                   for name in request.robot_positions}
        adapter = EmosNativeAdapter(request, robot_resume=resumes, endpoint=endpoint,
                                    source_root=args.source_root, log_dir=folder / (args.mode + "_native_logs"))
        adapter.initialize({"evidence_scope": "synthetic_protocol_fixture"}, {
            "robot_positions": request.robot_positions,
            "scene_description": "Synthetic fixture with no physical execution. Native [x,z,y] "
                "coordinates: object one at [1,0,0], object two at [2,0,0]. Both objects are visible.",
        })
        with (folder / (args.mode + "_native_stdout.txt")).open("w") as stream, redirect_stdout(stream):
            decision = adapter(package, robots)
        receipt.update(status="protocol_passed" if args.mode == "protocol" else "live_planner_returned",
                       decision={"condition": decision.condition.to_dict(),
                                 "assignments": [asdict(x) for x in decision.assignments],
                                 "unassigned_tasks": list(decision.unassigned_tasks),
                                 "provenance": dict(decision.provenance)})
        exit_code = 0
    except NativeEmosUnavailable as exc:
        receipt.update(status="blocked", reason=str(exc))
    except (NativeEmosError, ValueError) as exc:
        receipt.update(status="failed_closed", error_type=type(exc).__name__, reason=str(exc))
    finally:
        if endpoint is not None:
            receipt.update(endpoint=endpoint.receipt(), model_requests=endpoint.calls)
        if adapter is not None:
            receipt["raw_native_subtasks"] = {key: value.subtask_description
                                               for key, value in adapter.native_results.items()}
            adapter.close()
        path = folder / (args.mode + "_probe.json")
        path.write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps({"status": receipt["status"], "receipt": str(path),
                          "algorithm_acceptance": receipt["algorithm_acceptance"]}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
