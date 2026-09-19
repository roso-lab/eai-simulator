"""Pure benchmark eligibility contract for team and baseline scenarios.

The feasibility oracle answers whether a phase graph has a capability/payload
set cover. Heterogeneous-team candidates must declare capacities, require more
than one robot, require different robot types, and have no single-robot
counterfactual. F-COV may explicitly declare ``single_agent_baseline`` so
single-agent and same-type coverage controls remain admissible. A prescribed
escort waypoint cannot be the only source of a team claim.
"""

from __future__ import annotations

import math
from numbers import Real
from typing import Any

from .oracle import _eligible_agents, minimum_team_certificate
from .schema import ROBOT_PROFILES


def _finding(check: str, passed: bool, message: str, **details: Any) -> dict[str, Any]:
    """Build one stable, machine-readable contract finding."""
    result = {
        "check": check,
        "status": "pass" if passed else "fail",
        "message": message,
    }
    if details:
        result["details"] = details
    return result


def _payload_policy_finding(data: dict[str, Any], payload_policy: Any) -> dict[str, Any]:
    robot_types = sorted({robot.get("type") for robot in data.get("robots", ())})
    if not isinstance(payload_policy, dict) or not payload_policy:
        return _finding(
            "payload_policy",
            False,
            "benchmark eligibility requires a non-empty payload_policy",
            missing_types=robot_types,
        )

    unknown = sorted(set(payload_policy) - set(ROBOT_PROFILES))
    missing = sorted(set(robot_types) - set(payload_policy))
    invalid = sorted(
        key for key, value in payload_policy.items()
        if not isinstance(value, Real)
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) <= 0
    )
    passed = not unknown and not missing and not invalid
    return _finding(
        "payload_policy",
        passed,
        "payload_policy covers every participating robot type"
        if passed
        else "payload_policy must provide one positive finite capacity per participating robot type",
        unknown_types=unknown,
        missing_types=missing,
        invalid_types=invalid,
    )


def _core_nodes_without_escort_prerequisites(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Remove navigation nodes used only as an escort prerequisite.

    An escort waypoint is operationally useful to an executor, but it does
    not establish heterogeneous necessity on its own.  Keep all substantive
    nodes and navigation nodes that are not prerequisites of a substantive
    phase.  The resulting graph is only used for a capability counterfactual;
    the input scenario is never mutated.
    """
    nodes = list(data.get("phase_graph", {}).get("nodes", ()))
    substantive_ids = {
        node.get("id")
        for node in nodes
        if node.get("skill_key") not in {"navigate", "wait"}
    }
    node_by_id = {node.get("id"): node for node in nodes}
    escort_ids = set()
    for node in nodes:
        if node.get("skill_key") in {"navigate", "wait"}:
            continue
        for prerequisite in node.get("prerequisites", ()):
            candidate = node_by_id.get(prerequisite)
            if not candidate or candidate.get("skill_key") != "navigate":
                continue
            # Every prerequisite of a joint transport is part of the
            # substantive team binding. Removing even an unpinned approach
            # would create a false single-agent counterfactual.
            if node.get("skill_key") == "joint_transport":
                continue
            escort_ids.add(prerequisite)
    if not substantive_ids or not escort_ids:
        return nodes
    return [node for node in nodes if node.get("id") not in escort_ids]


def _single_agent_counterfactual(data: dict[str, Any], nodes: list[dict[str, Any]], payload_policy: dict) -> list[str]:
    """Return robots that cover every node in a counterfactual graph.

    A declared multi-member ``joint_transport`` remains infeasible for any
    single robot even when the generic navigation capability check accepts
    that robot.  The oracle's minimum-team certificate applies this binding;
    the escort counterfactual must apply it too.
    """
    robots = data.get("robots", ())
    feasible = []
    for robot in robots:
        def covers(node: dict[str, Any]) -> bool:
            if node.get("skill_key") == "joint_transport":
                declared_team = set(node.get("agents") or ())
                if len(declared_team) > 1:
                    return False
            return robot["id"] in _eligible_agents(
                data, node, payload_policy, robots=[robot]
            )[0]

        if all(covers(node) for node in nodes):
            feasible.append(robot["id"])
    return feasible


def benchmark_eligibility_contract(
    data: dict[str, Any], payload_policy: dict[str, float] | None = None
) -> dict[str, Any]:
    """Check whether a scenario is admissible as a heterogeneous-team benchmark.

    The return value is deliberately report-shaped and contains one pass/fail
    finding for each admission rule.  It is pure: ``data`` and
    ``payload_policy`` are not modified, and no simulator/provider imports are
    performed.
    """
    policy_finding = _payload_policy_finding(data, payload_policy)
    certificate_policy = payload_policy if policy_finding["status"] == "pass" else None
    cert = minimum_team_certificate(data, payload_policy=certificate_policy)
    minimum_size = cert.get("minimum_team_size")
    single_agent = list(cert.get("single_agent_feasible") or [])
    forced_heterogeneity = bool(cert.get("forced_heterogeneity"))

    findings = [policy_finding]
    comparison_mode = (
        "single_agent_baseline"
        if data.get("benchmark_task_id") == "F-COV-01"
        else "heterogeneous_team"
    )
    allow_single_agent_baseline = comparison_mode == "single_agent_baseline"
    findings.append(_finding(
        "minimum_team_size",
        (isinstance(minimum_size, int) and minimum_size >= 1
         and (allow_single_agent_baseline or minimum_size > 1)),
        ("task has a valid minimum team size for its declared comparison mode"
         if allow_single_agent_baseline
         else "minimum team contains more than one robot"
         if isinstance(minimum_size, int) and minimum_size > 1
         else "benchmark task must require a team larger than one robot"),
        value=minimum_size,
    ))
    findings.append(_finding(
        "forced_heterogeneity",
        forced_heterogeneity or allow_single_agent_baseline,
        ("not required for the declared single-agent baseline mode"
         if allow_single_agent_baseline
         else "every minimum team contains more than one robot type"
         if forced_heterogeneity
         else "minimum teams do not force heterogeneous robot types"),
        value=forced_heterogeneity,
    ))
    findings.append(_finding(
        "single_agent_feasible",
        bool(single_agent) if allow_single_agent_baseline else not single_agent,
        ("single-agent counterfactual is recorded for the baseline"
         if allow_single_agent_baseline
         else "no single robot covers the complete phase graph"
         if not single_agent
         else "a single robot can complete the complete phase graph"),
        value=single_agent,
    ))

    escort_nodes = _core_nodes_without_escort_prerequisites(data)
    escort_counterfactual = _single_agent_counterfactual(
        data, escort_nodes, payload_policy or {}) if policy_finding["status"] == "pass" else []
    escort_only = bool(escort_counterfactual) and len(escort_nodes) < len(
        data.get("phase_graph", {}).get("nodes", ())
    )
    escort_message = (
        "baseline counterfactual recorded without claiming heterogeneous necessity"
        if allow_single_agent_baseline else
        "heterogeneous necessity remains after removing escort-only prerequisites"
        if not escort_only else
        "escort-only prerequisite is the sole source of team necessity"
    )
    findings.append(_finding(
        "escort_prerequisite_only",
        not escort_only,
        escort_message,
        single_agent_counterfactual=escort_counterfactual,
    ))

    passed = all(item["status"] == "pass" for item in findings)
    return {
        "status": "pass" if passed else "fail",
        "eligible": passed,
        "comparison_mode": comparison_mode,
        "findings": findings,
        "minimum_team": cert,
        "counterfactual": {
            "nodes_checked": [node.get("id") for node in escort_nodes],
            "single_agent_feasible": escort_counterfactual,
        },
    }


# Short aliases for callers that prefer verb-oriented names.
benchmark_eligibility = benchmark_eligibility_contract
check_benchmark_eligibility = benchmark_eligibility_contract
validate_benchmark_contract = benchmark_eligibility_contract

__all__ = [
    "benchmark_eligibility_contract",
    "benchmark_eligibility",
    "check_benchmark_eligibility",
    "validate_benchmark_contract",
]
