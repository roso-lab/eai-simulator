"""Frozen candidate-pool profiles for goal-task authoring.

The legacy four-member pools remain the release default.  ``expanded_v1`` is
an opt-in authoring profile that increases selection entropy while preserving
the public task schema.  It records capability signatures and a declared
activation cost so allocation mechanisms can measure team-size trade-offs
without pretending that the cost is an energy measurement.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from itertools import combinations_with_replacement
from typing import Any, Mapping, Sequence

from .goal_driven_fixtures import robot_physical_profile
from .provenance import canonical_sha256


POOL_DESIGN_SCHEMA = "eai.benchmark.candidate_pool_design"
POOL_DESIGN_VERSION = 2
POOL_PROFILES = ("legacy_v1", "expanded_v1")
NECESSITY_CERTIFICATE_SCHEMA = "eai.benchmark.capability_signature_necessity_certificate"
NECESSITY_CERTIFICATE_VERSION = 1
_NECESSITY_SCOPE = "capability_signature_declaration_sampling_only"
_DECLARATION_RESULTS = {"unknown", "declared_compatible", "declared_incompatible"}


def capability_signature(robot: Mapping[str, Any]) -> str:
    """Return a stable, coarse signature for counterfactual sampling.

    The signature is a grouping key, not a hidden role label.  It includes the
    public skills/interfaces and the frozen physical capacity classes used by
    the authoring review.  Exact values remain available in the public robot
    declaration for protocol and constraint validation.
    """
    profile = robot["physical_profile"]
    payload_class = "none" if profile["effective_payload_kg"] <= 0 else (
        "light" if profile["effective_payload_kg"] < 8 else "heavy")
    reach_class = "none" if profile["reach_m"] <= 0 else (
        "short" if profile["reach_m"] < 0.6 else "standard")
    return "|".join((
        str(robot["mobility"]),
        ",".join(sorted(set(robot["public_capabilities"]))),
        ",".join(sorted(set(robot["interfaces"]))),
        payload_class,
        reach_class,
    ))


def activation_cost(robot: Mapping[str, Any]) -> float:
    """Return the frozen unit cost used for allocation comparisons.

    This is an authoring cost proxy (one active robot plus a type overhead),
    not measured battery energy.  A future execution scorer must replace it
    with an explicitly validated metric before reporting ``energy_j``.
    """
    type_overhead = {"iris": 1.2, "cf2x": 0.8, "m20": 1.0, "carter": 1.1,
                     "go2": 1.0, "b2": 1.1, "lite3": 0.9}
    return float(type_overhead.get(robot["type"], 1.0))


def _row(identifier: str, robot_type: str, mobility: str, capabilities: Sequence[str],
         interfaces: Sequence[str]) -> dict[str, Any]:
    return {
        "id": identifier,
        "type": robot_type,
        "mobility": mobility,
        "public_capabilities": sorted(set(capabilities)),
        "interfaces": sorted(set(interfaces)),
        "physical_profile": robot_physical_profile(robot_type),
    }


def expanded_additions(scene_key: str) -> list[dict[str, Any]]:
    """Return four deliberate alternatives for a scene's legacy pool.

    Each scene gets one aerial alternative, two ground alternatives with
    overlapping navigation/observation skills, and one weaker ground decoy.
    Warehouse and house alternatives expose the shared transport interface,
    while their lower payload keeps them from silently becoming a new witness.
    """
    prefix = {"factory": "factory", "warehouse_nvd": "warehouse",
              "modern_house_nvd": "house", "city": "city"}.get(scene_key)
    if prefix is None:
        raise ValueError(f"unsupported candidate-pool scene: {scene_key}")
    if scene_key == "factory":
        ground = [
            _row(f"{prefix}_ground_4", "go2", "ground",
                 ["ground_navigation", "observe", "stair_traversal", "relay"],
                 ["camera", "event_bus"]),
            _row(f"{prefix}_ground_5", "b2", "ground",
                 ["ground_navigation", "observe", "stair_traversal", "relay"],
                 ["camera", "event_bus"]),
            _row(f"{prefix}_ground_6", "lite3", "ground",
                 ["ground_navigation", "observe"], ["camera"]),
        ]
    elif scene_key in {"warehouse_nvd", "modern_house_nvd"}:
        ground = [
            _row(f"{prefix}_ground_4", "b2", "ground",
                 ["ground_navigation", "observe", "cooperative_carry", "synchronize"],
                 ["camera", "cooperative_transport", "event_bus"]),
            _row(f"{prefix}_ground_5", "go2", "ground",
                 ["ground_navigation", "observe", "cooperative_carry", "synchronize"],
                 ["camera", "cooperative_transport", "event_bus"]),
            _row(f"{prefix}_ground_6", "lite3", "ground",
                 ["ground_navigation", "observe"], ["camera"]),
        ]
    else:
        ground = [
            _row("city_ground_2", "b2", "ground",
                 ["ground_navigation", "relay", "guide_people", "control_following_traffic"],
                 ["velocity_command", "event_bus"]),
            _row("city_ground_3", "go2", "ground",
                 ["ground_navigation", "relay", "guide_people", "control_following_traffic"],
                 ["velocity_command", "event_bus"]),
            _row("city_ground_4", "lite3", "ground",
                 ["ground_navigation", "relay"], ["velocity_command", "event_bus"]),
        ]
    aerial_id = f"{prefix}_aerial_2" if scene_key != "city" else "city_aerial_2"
    aerial = _row(aerial_id, "cf2x", "aerial",
                  ["aerial_navigation", "observe", "relay"],
                  ["camera", "event_bus"])
    if scene_key == "city":
        ground.append(_row("city_aerial_3", "iris", "aerial",
                           ["aerial_navigation", "observe", "relay"],
                           ["camera", "event_bus"]))
    return [aerial, *ground]


def apply_pool_profile(public_task: Mapping[str, Any], profile: str) -> dict[str, Any]:
    """Return a copy of ``public_task`` with an opt-in expanded pool."""
    if profile not in POOL_PROFILES:
        raise ValueError(f"unsupported candidate-pool profile: {profile}")
    result = deepcopy(dict(public_task))
    if profile == "expanded_v1":
        existing = {row["id"] for row in result["candidate_robot_pool"]}
        additions = expanded_additions(result["mission"]["scene_key"])
        if existing & {row["id"] for row in additions}:
            raise ValueError("expanded candidate pool id collides with legacy pool")
        result["candidate_robot_pool"].extend(additions)
    return result


def _validate_profile_pool_identity(public_task: Mapping[str, Any], profile: str) -> None:
    """Bind the authoring profile to the frozen scene pool, not a caller label."""
    # Imported lazily because goal_driven_tasks owns the scene builders and
    # imports this module for pool transformation.
    from .goal_driven_tasks import build_goal_task_spec

    expected = build_goal_task_spec(
        public_task["mission"]["scene_key"], pool_profile=profile)
    if list(public_task["candidate_robot_pool"]) != list(expected["candidate_robot_pool"]):
        raise ValueError("candidate pool does not match the declared frozen profile")


def _signature_groups(public_task: Mapping[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = {}
    for row in public_task["candidate_robot_pool"]:
        groups.setdefault(capability_signature(row), []).append(row["id"])
    return [
        {"signature": signature, "candidate_ids": sorted(ids)}
        for signature, ids in sorted(groups.items())
    ]


def _representative_teams(public_task: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Enumerate bounded representative teams by signature multiset.

    This is an authoring search description.  It deliberately records public
    declaration compatibility only; it is not a physics or task solver.
    """
    groups = _signature_groups(public_task)
    group_ids = {group["signature"]: group["candidate_ids"] for group in groups}
    signatures = [group["signature"] for group in groups]
    max_size = public_task["budgets"]["max_active_agents"]
    teams: list[dict[str, Any]] = []
    for size in range(1, max_size + 1):
        for combo in combinations_with_replacement(signatures, size):
            counts = Counter(combo)
            if any(counts[signature] > len(group_ids[signature])
                   for signature in counts):
                continue
            ids: list[str] = []
            occurrences: Counter[str] = Counter()
            for signature in combo:
                occurrences[signature] += 1
                ids.append(group_ids[signature][occurrences[signature] - 1])
            teams.append({
                "team_key": "+".join(combo),
                "signature_combo": list(combo),
                "robot_ids": ids,
                "result": "unknown",
                "evidence_refs": [],
            })
    return teams


def _necessity_search_scope(public_task: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(public_task["candidate_robot_pool"])
    teams = _representative_teams(public_task)
    return {
        "strategy": "all_signature_multisets_up_to_max_active_agents",
        "candidate_ids": sorted(row["id"] for row in rows),
        "candidate_count": len(rows),
        "max_active_agents": public_task["budgets"]["max_active_agents"],
        "signature_groups": _signature_groups(public_task),
        "representative_team_count": len(teams),
    }


def build_capability_signature_necessity_certificate(
        public_task: Mapping[str, Any], profile: str, *,
        evaluations: Mapping[str, Mapping[str, Any]] | None = None,
        unknown_reason: str = "execution_evidence_pending") -> dict[str, Any]:
    """Build a declaration-level, signature-sampled necessity certificate.

    ``evaluations`` is intentionally keyed by the generated representative
    team key.  A non-unknown result must carry author evidence.  The overall
    state is derived from those rows, so authors cannot forge ``validated`` by
    changing a top-level flag.
    """
    if profile not in POOL_PROFILES:
        raise ValueError(f"unsupported candidate-pool profile: {profile}")
    _validate_profile_pool_identity(public_task, profile)
    if not isinstance(unknown_reason, str) or not unknown_reason:
        raise ValueError("unknown_reason must be a nonempty string")
    evaluations = {} if evaluations is None else dict(evaluations)
    teams = _representative_teams(public_task)
    expected_keys = {team["team_key"] for team in teams}
    if not set(evaluations) <= expected_keys:
        raise ValueError("necessity evaluation references an unknown representative team")
    normalized_teams = []
    for team in teams:
        entry = evaluations.get(team["team_key"], {})
        if not isinstance(entry, Mapping):
            raise ValueError("necessity evaluation must be an object")
        if set(entry) - {"result", "evidence_refs"}:
            raise ValueError("necessity evaluation fields mismatch")
        result = entry.get("result", "unknown")
        raw_refs = entry.get("evidence_refs", [])
        if not isinstance(raw_refs, (list, tuple, set)):
            raise ValueError("necessity evaluation evidence_refs must be a list")
        refs = sorted(set(raw_refs))
        if result not in _DECLARATION_RESULTS:
            raise ValueError("necessity evaluation has an invalid declaration result")
        if any(not isinstance(ref, str) or not ref for ref in refs):
            raise ValueError("necessity evaluation evidence_refs must be nonempty strings")
        if result == "unknown" and refs:
            raise ValueError("unknown necessity evaluation cannot carry evidence_refs")
        if result != "unknown" and not refs:
            raise ValueError("declared necessity result requires evidence_refs")
        normalized_teams.append({**team, "result": result, "evidence_refs": refs})

    compatible = [team for team in normalized_teams
                  if team["result"] == "declared_compatible"]
    unknown = [team for team in normalized_teams if team["result"] == "unknown"]
    if compatible:
        status = "disproven"
        counterexample = {
            "team_key": compatible[0]["team_key"],
            "signature_combo": compatible[0]["signature_combo"],
            "robot_ids": compatible[0]["robot_ids"],
            "evidence_refs": compatible[0]["evidence_refs"],
        }
        resolved_reason = None
    elif unknown:
        status = "unknown"
        counterexample = None
        resolved_reason = unknown_reason
    else:
        status = "validated"
        counterexample = None
        resolved_reason = None
    evidence_refs = sorted({ref for team in normalized_teams for ref in team["evidence_refs"]})
    value = {
        "schema": NECESSITY_CERTIFICATE_SCHEMA,
        "version": NECESSITY_CERTIFICATE_VERSION,
        "scene_key": public_task["mission"]["scene_key"],
        "profile": profile,
        "scope": _NECESSITY_SCOPE,
        "search_scope": _necessity_search_scope(public_task),
        "representative_teams": normalized_teams,
        "status": status,
        "evidence_refs": evidence_refs,
        "counterexample": counterexample,
        "unknown_reason": resolved_reason,
        "physical_execution_validated": False,
        "whole_task_necessity_validated": False,
    }
    return validate_capability_signature_necessity_certificate(value, public_task)["normalized"]


def validate_capability_signature_necessity_certificate(
        value: Mapping[str, Any], public_task: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema", "version", "scene_key", "profile", "scope", "search_scope",
        "representative_teams", "status", "evidence_refs", "counterexample",
        "unknown_reason", "physical_execution_validated", "whole_task_necessity_validated",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("capability necessity certificate fields mismatch")
    if value["schema"] != NECESSITY_CERTIFICATE_SCHEMA or value["version"] != NECESSITY_CERTIFICATE_VERSION:
        raise ValueError("unsupported capability necessity certificate schema")
    if value["scene_key"] != public_task["mission"]["scene_key"] or value["profile"] not in POOL_PROFILES:
        raise ValueError("capability necessity certificate task binding mismatch")
    _validate_profile_pool_identity(public_task, value["profile"])
    if value["scope"] != _NECESSITY_SCOPE:
        raise ValueError("capability necessity certificate scope must remain declaration-level")
    if value["search_scope"] != _necessity_search_scope(public_task):
        raise ValueError("capability necessity certificate search scope mismatch")
    if type(value["physical_execution_validated"]) is not bool or type(value["whole_task_necessity_validated"]) is not bool:
        raise ValueError("capability necessity certificate flags must be booleans")
    if value["physical_execution_validated"] or value["whole_task_necessity_validated"]:
        raise ValueError("capability necessity certificate cannot claim physical or whole-task necessity")
    teams = value["representative_teams"]
    expected_teams = {team["team_key"]: team for team in _representative_teams(public_task)}
    if (not isinstance(teams, list) or len(teams) != len(expected_teams)
            or any(not isinstance(team, Mapping) for team in teams)
            or {team.get("team_key") for team in teams} != set(expected_teams)):
        raise ValueError("capability necessity certificate representative-team coverage mismatch")
    seen_refs: set[str] = set()
    compatible = []
    unknown = []
    for team in teams:
        if set(team) != {"team_key", "signature_combo", "robot_ids", "result", "evidence_refs"}:
            raise ValueError("representative team fields mismatch")
        expected_team = expected_teams[team["team_key"]]
        if (team["signature_combo"] != expected_team["signature_combo"]
                or team["robot_ids"] != expected_team["robot_ids"]):
            raise ValueError("representative team does not match the public signature grouping")
        if team["result"] not in _DECLARATION_RESULTS:
            raise ValueError("representative team has an invalid declaration result")
        refs = team["evidence_refs"]
        if (not isinstance(refs, list) or len(refs) != len(set(refs))
                or any(not isinstance(ref, str) or not ref for ref in refs)):
            raise ValueError("representative team evidence_refs must be unique nonempty strings")
        if team["result"] == "unknown":
            if refs:
                raise ValueError("unknown representative team cannot carry evidence_refs")
            unknown.append(team)
        else:
            if not refs:
                raise ValueError("declared necessity result requires evidence_refs")
            seen_refs.update(refs)
        if team["result"] == "declared_compatible":
            compatible.append(team)
    if value["evidence_refs"] != sorted(seen_refs):
        raise ValueError("capability necessity certificate evidence_refs mismatch")
    status = value["status"]
    if status not in {"validated", "disproven", "unknown"}:
        raise ValueError("capability necessity certificate has an invalid status")
    if compatible and status != "disproven":
        raise ValueError("a declared-compatible counterexample requires disproven status")
    if not compatible and unknown and status != "unknown":
        raise ValueError("incomplete signature evidence requires unknown status")
    if not compatible and not unknown and status != "validated":
        raise ValueError("complete incompatible signature evidence requires validated status")
    if status == "unknown" and (not isinstance(value["unknown_reason"], str) or not value["unknown_reason"]):
        raise ValueError("unknown capability necessity requires unknown_reason")
    if status != "unknown" and value["unknown_reason"] is not None:
        raise ValueError("resolved capability necessity cannot carry unknown_reason")
    counterexample = value["counterexample"]
    if status == "disproven":
        if not isinstance(counterexample, Mapping) or counterexample.get("team_key") not in {team["team_key"] for team in compatible}:
            raise ValueError("disproven capability necessity requires a compatible counterexample")
        matching = next(team for team in compatible if team["team_key"] == counterexample["team_key"])
        if dict(counterexample) != {
                "team_key": matching["team_key"], "signature_combo": matching["signature_combo"],
                "robot_ids": matching["robot_ids"], "evidence_refs": matching["evidence_refs"]}:
            raise ValueError("capability necessity counterexample does not match representative evidence")
    elif counterexample is not None:
        raise ValueError("only disproven capability necessity may carry a counterexample")
    return {"validated": True, "normalized": deepcopy(dict(value)),
            "sha256": canonical_sha256(value)}


def pool_design_summary(public_task: Mapping[str, Any], profile: str) -> dict[str, Any]:
    """Build validated author-only metadata for a candidate pool."""
    if profile not in POOL_PROFILES:
        raise ValueError(f"unsupported candidate-pool profile: {profile}")
    _validate_profile_pool_identity(public_task, profile)
    rows = list(public_task["candidate_robot_pool"])
    signatures = Counter(capability_signature(row) for row in rows)
    return {
        "schema": POOL_DESIGN_SCHEMA,
        "version": POOL_DESIGN_VERSION,
        "profile": profile,
        "scene_key": public_task["mission"]["scene_key"],
        "candidate_count": len(rows),
        "max_active_agents": public_task["budgets"]["max_active_agents"],
        "capability_signature_counts": dict(sorted(signatures.items())),
        "activation_costs": {row["id"]: activation_cost(row) for row in rows},
        "cost_semantics": "frozen_authoring_proxy_per_active_robot",
        "counterfactual_search": "capability_signature_sampling",
        "necessity_certificate": build_capability_signature_necessity_certificate(public_task, profile),
        "execution_validated": False,
        "scoreable": False,
    }


def validate_pool_design_summary(summary: Mapping[str, Any], public_task: Mapping[str, Any]) -> dict[str, Any]:
    expected = {"schema", "version", "profile", "scene_key", "candidate_count",
                "max_active_agents", "capability_signature_counts", "activation_costs",
                "cost_semantics", "counterfactual_search", "necessity_certificate",
                "execution_validated", "scoreable"}
    if set(summary) != expected:
        raise ValueError("candidate pool design fields mismatch")
    if summary["schema"] != POOL_DESIGN_SCHEMA or summary["version"] != POOL_DESIGN_VERSION:
        raise ValueError("unsupported candidate pool design schema")
    if summary["scene_key"] != public_task["mission"]["scene_key"]:
        raise ValueError("candidate pool design scene mismatch")
    if summary["candidate_count"] != len(public_task["candidate_robot_pool"]):
        raise ValueError("candidate pool design count mismatch")
    if summary["max_active_agents"] != public_task["budgets"]["max_active_agents"]:
        raise ValueError("candidate pool design budget mismatch")
    if summary["necessity_certificate"].get("profile") != summary["profile"]:
        raise ValueError("candidate pool necessity certificate profile mismatch")
    validate_capability_signature_necessity_certificate(summary["necessity_certificate"], public_task)
    expected_summary = pool_design_summary(public_task, summary["profile"])
    if dict(summary) != expected_summary:
        raise ValueError("candidate pool design summary does not match public pool")
    return {"validated": True, "summary": deepcopy(expected_summary)}


__all__ = [
    "NECESSITY_CERTIFICATE_SCHEMA", "NECESSITY_CERTIFICATE_VERSION",
    "POOL_DESIGN_SCHEMA", "POOL_DESIGN_VERSION", "POOL_PROFILES",
    "activation_cost", "apply_pool_profile", "capability_signature",
    "build_capability_signature_necessity_certificate", "expanded_additions",
    "pool_design_summary", "validate_capability_signature_necessity_certificate",
    "validate_pool_design_summary",
]
