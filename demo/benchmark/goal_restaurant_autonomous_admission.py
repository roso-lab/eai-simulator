"""Static, evaluator-only coverage gate for the full restaurant condition.

This inventory describes the shipped synchronous backend, not robot ability.
It never edits the public roster, certifies physics, or supplies a feasibility
label.  It is deliberately separate from the legacy three-robot diagnostic.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from .goal_deployment_assessment import enable_deployment_assessment
from .goal_driven_tasks import build_goal_task_package
from .provenance import canonical_sha256, file_sha256


SCHEMA = "eai.restaurant.autonomous_admission.v1"
ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    "demo/benchmark/goal_restaurant_autonomous_admission.py",
    "demo/benchmark/goal_restaurant_algorithm_episode.py",
    "demo/benchmark/goal_restaurant_episode.py",
    "demo/benchmark/goal_driven_tasks.py",
    "demo/benchmark/goal_deployment_assessment.py",
    "demo/restaurant_benchmark/config.py",
)


class RestaurantPlatformCoverageError(RuntimeError):
    """A platform limitation before participant measurement, not a rejection."""

    def __init__(self, report: Mapping[str, Any]):
        self.report = deepcopy(dict(report))
        super().__init__("restaurant autonomous condition is platform_blocked")


def build_restaurant_autonomous_admission() -> dict[str, Any]:
    """Recompute the baseline full8 gate from local maintained source.

    No participant-supplied roster, bindings, hashes or coverage assertions are
    accepted.  This is one baseline condition, not all 81 restaurant variants.
    A source registration means a handler exists; it does not prove its whole
    argument domain, dynamic transitions, or physical success.
    """
    from .goal_restaurant_algorithm_episode import (
        AGENT_PHYSICAL_LEGS, ROBOT_BINDINGS,
    )

    bundle = build_goal_task_package("restaurant")
    original = bundle["public_task"]
    public = enable_deployment_assessment(original)
    world = public["public_world"]
    candidates = public["candidate_robot_pool"]
    rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for robot in candidates:
        agent = robot["id"]
        binding = ROBOT_BINDINGS.get(agent)
        if binding is None:
            gaps.append({"kind": "missing_native_binding", "agent_id": agent})
        # Reviewed against RestaurantMultiAgentGoalBackend and the actual
        # navigator types in run_worker. Iris uses SharedClockRelayDriver;
        # it has no drive/observe_region implementation.
        handlers = ({"ground_navigation", "observe", "relay"}
                    if binding and robot["type"] == "carter" else
                    {"relay"} if binding and robot["type"] == "iris" else set())
        for skill in robot["public_capabilities"]:
            present = skill in handlers
            row = {"agent_id": agent, "skill": skill,
                   "handler_registered": present, "routes": []}
            if not present:
                gaps.append({"kind": "missing_skill_handler", "agent_id": agent,
                             "skill": skill})
            if skill in {"ground_navigation", "aerial_navigation"}:
                for route in world["routes"]:
                    if skill not in route["access_modes"]:
                        continue
                    legs = [leg_id for leg_id, leg in AGENT_PHYSICAL_LEGS.items()
                            if binding and leg["agent"] == binding["native_agent_id"]
                            and leg["from_region_id"] == route["from_region_id"]
                            and leg["to_region_id"] == route["to_region_id"]]
                    registered = present and len(legs) == 1
                    row["routes"].append({"route_id": route["id"],
                                          "physical_leg_ids": sorted(legs),
                                          "registered": registered})
                    if not registered:
                        gaps.append({"kind": "missing_route_driver", "agent_id": agent,
                                     "skill": skill, "route_id": route["id"]})
                row["argument_scope"] = "one public route per call; current-region origin"
            elif skill == "observe" and present:
                row["argument_scope"] = "kitchen_service or dining_service after arrival"
            elif skill == "relay" and present:
                row["argument_scope"] = "legacy readiness v1 publication at current region"
            else:
                row["argument_scope"] = "unavailable"
            rows.append(row)

    # These are implementation gates, never synthetic member_exit events or
    # evidence that the actual team has no legal remaining completion path.
    gates = [
        "full_argument_domain_not_validated",
        "autonomous_participant_native_entrypoint_not_wired",
        "readiness_v2_native_transport_not_wired",
        "concurrent_execution_and_inflight_event_pump_not_implemented",
        "matching_native_remaining_feasibility_oracle_not_verified",
    ]
    report = {
        "schema": SCHEMA,
        "scope": "baseline_full8_static_source_inventory",
        "status": "platform_blocked",
        "failure_category": "platform_execution_coverage_gap",
        "participant_called": False,
        "participant_decision": None,
        "feasibility_label": None,
        "oracle_status": "failed",
        "original_public_task_sha256": canonical_sha256(original),
        "effective_public_task_sha256": canonical_sha256(public),
        "task_package_sha256": canonical_sha256(bundle),
        "candidate_ids": [robot["id"] for robot in candidates],
        "robot_bindings": deepcopy(ROBOT_BINDINGS),
        "coverage": rows,
        "coverage_gaps": gaps,
        "remaining_gates": gates,
        "source_sha256": {name: file_sha256(ROOT / name) for name in SOURCES},
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }
    report["report_sha256"] = canonical_sha256(report)
    return report


def validate_restaurant_autonomous_admission(report: Mapping[str, Any]) -> dict[str, Any]:
    """Bind an inventory to this source revision, not merely its own digest."""
    expected = build_restaurant_autonomous_admission()
    # Canonical comparison distinguishes booleans from numbers and rejects
    # extra fields. Rehashed partial rosters and forged 'ready' flags fail.
    if canonical_sha256(report) != canonical_sha256(expected):
        raise ValueError("restaurant autonomous admission differs from maintained source")
    return expected


def require_restaurant_autonomous_coverage() -> None:
    """Call before initializing any autonomous participant for this condition."""
    report = build_restaurant_autonomous_admission()
    if report["status"] != "ready":
        raise RestaurantPlatformCoverageError(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_restaurant_autonomous_admission()
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 2 if report["status"] == "platform_blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
