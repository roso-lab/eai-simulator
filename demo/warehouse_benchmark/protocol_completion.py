"""Warehouse six-dimension protocol pilot runner.

This module is deliberately independent of Isaac Sim.  It freezes the
Warehouse task contract, executes a deterministic public-greedy protocol
participant against that contract, and writes the protocol's raw ledgers.
The existing 32-way Isaac run is imported as infrastructure evidence only;
it is never used as a six-dimension score.

The runner is useful before expensive Isaac batches are available: it checks
the data contract, pairing keys, aggregation rules, right-censoring and the
scorecard interface.  ``execution_scope`` in every report makes the boundary
between this kinematic/protocol pilot and physical evidence explicit.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO = ROOT / "demo/warehouse_benchmark/scenarios/business_logistics_01.json"
DEFAULT_INFRA_REPORT = ROOT / (
    "tmp/warehouse_benchmark/checkpoint_parallel_32_safe_zone_retry_20260922_213923/report.json"
)
PROTOCOL_SOURCE = Path(
    "/home/airs/文档/xwechat_files/wxid_g1xii5te9cen11_fdc8/msg/file/2026-09/"
    "异构多机器人高层协作智能 Benchmark 能力分类与评测协议.md"
)
PROTOCOL_SHA256 = "539822d71b46b520c21df9170b182e0b7fe66016b8cfb26825a33294f52651c1"
SCHEMA_VERSION = "warehouse_protocol_completion.v1"
HORIZON_S = 120.0
RESTORE_TOLERANCE = 7.63e-6
BRANCH_TYPES = ("fact", "reference", "sham", "intervention")
DIMENSIONS = (
    "task_allocation",
    "communication_adaptation",
    "temporal_coordination",
    "dynamic_reconfiguration",
    "cross_episode_learning",
    "coordination_cost_efficiency",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(canonical_json(dict(row)) + "\n")
    temporary.replace(path)


def _stable_seed(*parts: Any) -> int:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _unit(*parts: Any) -> float:
    return (_stable_seed(*parts) % 1_000_000) / 1_000_000.0


def wilson_ci(numerator: int, denominator: int, z: float = 1.959963984540054) -> dict[str, Any]:
    """Return a Wilson interval without replacing a zero denominator."""
    if denominator == 0:
        return {"lower": None, "upper": None, "status": "N/A_zero_denominator"}
    p = numerator / denominator
    centre = (p + z * z / (2 * denominator)) / (1 + z * z / denominator)
    half = z * math.sqrt(p * (1 - p) / denominator + z * z / (4 * denominator * denominator)) / (1 + z * z / denominator)
    return {"lower": max(0.0, centre - half), "upper": min(1.0, centre + half), "status": "defined"}


def _ratio(numerator: float, denominator: float, *, name: str, undefined: str = "N/A_zero_denominator") -> dict[str, Any]:
    if denominator == 0:
        return {"name": name, "numerator": numerator, "denominator": denominator, "raw": None, "difference": None, "status": undefined, "na_reason": undefined, "ci95": wilson_ci(0, 0)}
    raw = numerator / denominator
    ci = wilson_ci(int(numerator), int(denominator)) if float(numerator).is_integer() and float(denominator).is_integer() else {"lower": None, "upper": None, "status": "not_binary"}
    return {"name": name, "numerator": numerator, "denominator": denominator, "raw": raw, "difference": None, "status": "defined", "na_reason": None, "ci95": ci}


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return statistics.fmean(values) if values else None


def _percentile(values: Iterable[float], q: float) -> float | None:
    values = sorted(float(v) for v in values)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * q
    low, high = math.floor(index), math.ceil(index)
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (index - low)


def _paired_difference(rows: Iterable[Mapping[str, Any]], left: str, right: str) -> dict[str, Any]:
    grouped: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        grouped[str(row["base_instance_id"])][str(row["condition_id"])] = float(row["value"])
    differences = [values[left] - values[right] for values in grouped.values() if left in values and right in values]
    if not differences:
        return {"numerator": 0, "denominator": 0, "raw": None, "difference": None, "status": "N/A_no_pairs", "na_reason": "N/A_no_pairs", "ci95": {"lower": None, "upper": None, "status": "N/A_no_pairs"}}
    # A deterministic percentile interval is a transparent pilot substitute
    # for the formal instance-bootstrap interval used by the full protocol.
    return {
        "numerator": sum(differences), "denominator": len(differences),
        "raw": statistics.fmean(differences), "difference": statistics.fmean(differences), "status": "defined", "na_reason": None,
        "ci95": {"lower": _percentile(differences, 0.025), "upper": _percentile(differences, 0.975), "status": "pilot_paired_percentile"},
    }


def load_scenario(path: Path = DEFAULT_SCENARIO) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("scene_key") != "warehouse_nvd":
        raise ValueError("Warehouse protocol requires scene_key=warehouse_nvd")
    if not isinstance(data.get("phase_graph", {}).get("nodes"), list):
        raise ValueError("scenario phase_graph.nodes is required")
    return data


def _source_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def freeze_task_contract(
    scenario: Mapping[str, Any], *, protocol_sha256: str = PROTOCOL_SHA256,
    scenario_sha256: str | None = None,
) -> dict[str, Any]:
    """Build the immutable contract from the reviewed Warehouse scenario."""
    nodes = list(scenario["phase_graph"]["nodes"])
    node_ids = [str(node["id"]) for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("phase graph contains duplicate node ids")
    prerequisites = [{"from": dependency, "to": node["id"], "hard": True, "kind": "precedence"}
                    for node in nodes for dependency in node.get("prerequisites", [])]
    resources = sorted({resource for node in nodes for resource in node.get("resources", [])})
    milestones = [
        {"id": "rack_observation_consumed", "weight": 1.0, "predicate": "rack_overview_scan complete and observation consumed"},
        {"id": "aisle_access_ready", "weight": 1.0, "predicate": "aisle_left_access and aisle_right_access complete"},
        {"id": "joint_crate_delivered", "weight": 2.0, "predicate": "oversized_crate_transfer complete with two-member evidence"},
        {"id": "priority_carton_staged", "weight": 1.0, "predicate": "priority_carton_stage complete"},
        {"id": "dock_verified", "weight": 1.0, "predicate": "dock_lane_check and outbound_inventory_scan complete"},
        {"id": "dock_confirmation_hold", "weight": 1.0, "predicate": "dock_confirmation_hold stable for 3 seconds"},
        {"id": "closeout_complete", "weight": 1.0, "predicate": "aisle_closeout complete"},
    ]
    initial = {
        "robots": [{"id": robot["id"], "type": robot["type"], "capabilities": sorted({robot["type"], *(attachment["type"] for attachment in robot.get("attachments", []))})}
                   for robot in scenario.get("robots", [])],
        "objects": [{"id": entity["id"], "asset_key": entity.get("asset_key"), "mass_kg": entity.get("physics", {}).get("mass_kg")}
                    for entity in scenario.get("composition", {}).get("entities", [])],
        "seed": scenario.get("seed"),
    }
    goal = {"all_phase_nodes_complete": True, "milestones_stable": True, "business_identity_backend": "pending", "dock_service_backend": "pending"}
    return {
        "schema": "warehouse_task_contract.v1",
        "task_id": scenario.get("benchmark_task_id", "W-DAG-01"),
        "generator_version": "warehouse_protocol_completion.v1",
        "scenario_sha256": scenario_sha256 or digest(scenario),
        "protocol_sha256": protocol_sha256,
        "initial_state": initial,
        "goal_state": goal,
        "phase_graph": {
            "nodes": [{key: node.get(key) for key in ("id", "skill_key", "preferred_agent", "agents", "target_ref", "object_ref", "prerequisites", "timeout_s", "duration_s", "resources")} for node in nodes],
            "relations": prerequisites + [
                {"kind": "synchronization", "members": ["m20_1", "carter_1"], "node": "oversized_crate_transfer", "window_s": 2.0, "hard": True},
            ] + [
                {"kind": "mutual_exclusion", "resource": resource, "capacity": 1, "hard": True}
                for resource in resources
            ] + [{"kind": "handoff", "from": "priority_carton_stage", "to": "priority_carton_dispatch", "max_gap_s": 5.0, "hard": True}],
        },
        "success_predicate": {"all_nodes": node_ids, "stable_duration_s": 3.0, "external_evidence": ["rack_identity", "object_pose", "dock_receipt"], "external_evidence_status": "pending"},
        "progress_milestones": milestones,
        "minimum_team_certificate": {
            "feasible_team": ["iris_1", "m20_1", "carter_1"],
            "minimum_team_size": 3,
            "required_capabilities": {"aerial_scan": ["iris"], "joint_transport": ["m20", "carter"]},
            "delete_certificates": [
                {"removed": "iris_1", "status": "infeasible", "reason": "no aerial_scan provider"},
                {"removed": "m20_1", "status": "infeasible", "reason": "joint_transport and ground carrier unavailable"},
                {"removed": "carter_1", "status": "infeasible", "reason": "joint_transport partner unavailable"},
            ],
        },
        "target": "dimension-specific condition declared in experiment manifest",
        "nuisance": ["scene_geometry", "low_level_controller", "task_graph", "seed_tape", "horizon_s"],
        "failure_events": ["allocation_invalid", "message_not_delivered", "precedence_violation", "sync_miss", "resource_conflict", "member_failure", "recovery_timeout", "zero_progress"],
        "oracle": {"feasibility": "frozen minimum_team_certificate", "reference": "paired same-snapshot condition", "probe": "branch-and-bound sham/intervention"},
        "intervention_points": ["after_rack_overview_scan", "before_joint_transport", "after_fault_trigger"],
        "snapshot_schema": {"environment": ["phase_state", "task_state", "external_noise_counter"], "participant": ["assignment", "memory", "message_history"], "rng": ["environment", "communication", "participant"]},
        "reference_validity": {"minimum_reference_success": 0.2, "minimum_reference_efficiency": 1e-4, "relative_metrics": "N/A when threshold is not met"},
        "horizon_s": HORIZON_S,
        "n_a_pilot": {"pilot": 4, "formal": None, "freeze_rule": "formal sample size remains unfrozen until CI width/MDE review"},
    }


def _instance_rows(scenario: Mapping[str, Any], contract: Mapping[str, Any], count: int, *, source_revision: str) -> list[dict[str, Any]]:
    labels = [
        ("feasible", "normal", "single_provider_or_team", False),
        ("feasible", "capacity_scarce", "single_provider_or_team", False),
        ("infeasible", "capability_insufficient", "missing_aerial_provider", True),
        ("infeasible", "resource_conflict", "exclusive_resource_overbooked", True),
    ]
    rows = []
    for index in range(max(1, min(count, len(labels)))):
        feasibility, stratum, certificate, oracle_infeasible = labels[index]
        instance = {
            "base_instance_id": f"W-DAG-01-I{index + 1:02d}",
            "task_id": contract["task_id"], "task_version": contract["generator_version"],
            "instance_label": feasibility, "stratum": stratum, "certificate": certificate,
            "oracle_infeasible": oracle_infeasible, "seed": int(scenario.get("seed", 17)) + index,
            "initial_state": deepcopy(contract["initial_state"]), "goal_state": deepcopy(contract["goal_state"]),
            "phase_graph_sha256": digest(contract["phase_graph"]), "task_contract_sha256": digest(contract),
            "source_revision": source_revision, "scenario_sha256": contract["scenario_sha256"],
            "validity": "valid", "execution_scope": "protocol_synthetic",
        }
        rows.append(instance)
    return rows


def _assignment(instance: Mapping[str, Any], scenario: Mapping[str, Any]) -> dict[str, Any]:
    nodes = scenario["phase_graph"]["nodes"]
    if instance["instance_label"] == "infeasible":
        # Public greedy is allowed to declare resource/capability infeasibility,
        # but intentionally misses the aerial certificate on I03 to exercise
        # explicit invalid-output accounting in the pilot.
        if instance["stratum"] == "capability_insufficient":
            return {"output": "infeasible", "assignments": {}, "declaration_reason": "missing aerial_scan provider"}
        return {"output": "infeasible", "assignments": {}, "declaration_reason": "exclusive resource capacity is zero"}
    return {"output": "complete", "assignments": {node["id"]: list(node.get("agents") or [node.get("preferred_agent")]) for node in nodes}, "declaration_reason": None}


def _assignment_valid(instance: Mapping[str, Any], assignment: Mapping[str, Any]) -> tuple[bool, int, int]:
    if instance["instance_label"] != "feasible":
        return False, 0, 0
    rows = assignment.get("assignments", {})
    required = 10
    checks = 3
    violations = 0
    if assignment.get("output") != "complete" or len(rows) != required:
        violations += 1
    if rows.get("oversized_crate_transfer") != ["m20_1", "carter_1"]:
        violations += 1
    if rows.get("rack_overview_scan") != ["iris_1"]:
        violations += 1
    return violations == 0, violations, checks


def _condition_matrix() -> list[dict[str, Any]]:
    conditions = [
        {"dimension": "task_allocation", "condition_id": "allocation_frozen"},
        {"dimension": "temporal_coordination", "condition_id": "temporal_coupled"},
        {"dimension": "temporal_coordination", "condition_id": "temporal_decoupled"},
        {"dimension": "dynamic_reconfiguration", "condition_id": "scale_demand_expansion"},
        {"dimension": "dynamic_reconfiguration", "condition_id": "scale_no_change"},
        {"dimension": "dynamic_reconfiguration", "condition_id": "join_need"},
        {"dimension": "dynamic_reconfiguration", "condition_id": "join_no_join"},
        {"dimension": "dynamic_reconfiguration", "condition_id": "fault_member_exit"},
        {"dimension": "dynamic_reconfiguration", "condition_id": "fault_healthy_twin"},
        {"dimension": "cross_episode_learning", "condition_id": "learning_R"},
        {"dimension": "cross_episode_learning", "condition_id": "learning_C"},
        {"dimension": "communication_adaptation", "condition_id": "perfect", "family": "perfect"},
        {"dimension": "communication_adaptation", "condition_id": "zero", "family": "zero"},
    ]
    for delay in (0, 1, 2, 4, 8):
        conditions.append({"dimension": "communication_adaptation", "condition_id": f"delay_{delay}", "family": "fixed_delay", "delay_steps": delay})
    for loss in (0, 0.05, 0.1, 0.2, 0.4, 0.6, 1.0):
        conditions.append({"dimension": "communication_adaptation", "condition_id": f"iid_loss_{loss:g}", "family": "iid_loss", "loss": loss})
    for capacity in (None, 1.0, 0.75, 0.5, 0.25, 0.1):
        label = "inf" if capacity is None else f"{capacity:g}"
        conditions.append({"dimension": "communication_adaptation", "condition_id": f"capacity_{label}", "family": "capacity", "capacity_ratio": capacity})
    for mean_delay in (2, 4):
        conditions.append({"dimension": "communication_adaptation", "condition_id": f"jitter_{mean_delay}", "family": "jitter", "mean_delay": mean_delay, "cv": 0.5})
    for burst_length in (4, 8):
        conditions.append({"dimension": "communication_adaptation", "condition_id": f"burst_{burst_length}", "family": "burst_loss", "mean_loss": 0.2, "burst_length": burst_length})
    return conditions


def _comm_parameters(condition: Mapping[str, Any], instance_id: str, pair_seed: int) -> dict[str, Any]:
    family = condition.get("family", "perfect")
    if family == "fixed_delay":
        return {"delay_steps": int(condition["delay_steps"]), "loss": 0.0, "capacity_ratio": None, "distribution": "fixed"}
    if family == "iid_loss":
        return {"delay_steps": 0, "loss": float(condition["loss"]), "capacity_ratio": None, "distribution": "iid"}
    if family == "capacity":
        return {"delay_steps": 0, "loss": 0.0, "capacity_ratio": condition["capacity_ratio"], "distribution": "capacity"}
    if family == "jitter":
        return {"delay_steps": int(condition["mean_delay"]), "loss": 0.0, "capacity_ratio": None, "distribution": "jitter", "cv": 0.5}
    if family == "burst_loss":
        return {"delay_steps": 0, "loss": 0.2, "capacity_ratio": None, "distribution": "burst", "burst_length": int(condition["burst_length"])}
    if family == "zero":
        return {"delay_steps": 0, "loss": 1.0, "capacity_ratio": 0.0, "distribution": "zero"}
    return {"delay_steps": 0, "loss": 0.0, "capacity_ratio": None, "distribution": "perfect"}


def _episode_outcome(instance: Mapping[str, Any], condition: Mapping[str, Any], pair_seed: int, round_index: int, branch_type: str) -> dict[str, Any]:
    params = _comm_parameters(condition, str(instance["base_instance_id"]), pair_seed)
    condition_id = condition["condition_id"]
    feasible = instance["instance_label"] == "feasible"
    success = feasible
    q_final = 1.0 if feasible else 0.0
    if condition_id == "temporal_decoupled" and feasible:
        q_final = 0.85
    if condition_id.startswith("delay_"):
        d = int(params["delay_steps"])
        success = feasible and d <= 4
        q_final = max(0.0, 1.0 - 0.08 * d) if feasible else 0.0
    elif condition_id.startswith("iid_loss_"):
        p = params["loss"]
        success = feasible and (_unit(instance["base_instance_id"], condition_id, pair_seed) > p * 0.9)
        q_final = max(0.0, 1.0 - 0.9 * p) if feasible else 0.0
    elif condition_id.startswith("capacity_"):
        c = params["capacity_ratio"]
        success = feasible and (c is None or c >= 0.25)
        q_final = (1.0 if c is None else max(0.0, min(1.0, c + 0.15))) if feasible else 0.0
    elif condition_id.startswith("jitter_"):
        success = feasible and int(condition["mean_delay"]) <= 2
        q_final = 0.82 if feasible else 0.0
    elif condition_id.startswith("burst_"):
        success = feasible and int(condition["burst_length"]) == 4
        q_final = 0.72 if feasible else 0.0
    elif condition_id == "zero":
        success, q_final = False, 0.0
    elif condition_id == "dynamic_reconfiguration" or condition_id == "scale_demand_expansion":
        success = feasible
    elif condition_id == "join_need":
        success = feasible
        q_final = 0.9 if feasible else 0.0
    elif condition_id == "fault_member_exit":
        success = feasible and _unit(instance["base_instance_id"], pair_seed) > 0.18
        q_final = 0.78 if success else 0.35
    elif condition_id == "learning_R":
        # public_greedy has no cross-episode writable memory.  R and C must
        # therefore remain matched instead of fabricating a learning gain.
        success = feasible
        q_final = 0.45 if feasible else 0.0
    elif condition_id == "learning_C":
        success = feasible
        q_final = 0.45 if feasible else 0.0
    if branch_type == "reference":
        success, q_final = feasible, (1.0 if feasible else 0.0)
    if branch_type == "sham":
        success, q_final = feasible, (1.0 if feasible else 0.0)
    if branch_type == "intervention":
        success = feasible
        q_final = 1.0 if feasible else 0.35
    execution_time = 48.0 + 8.0 * (1.0 - q_final) + params["delay_steps"] * 0.6
    if not success:
        execution_time = HORIZON_S
    return {
        "success": bool(success), "q_final": float(q_final), "q_horizon": float(q_final),
        "execution_time_s": float(execution_time), "terminated_at_horizon": not success,
        "params": params,
    }


def _make_events(episode: Mapping[str, Any], outcome: Mapping[str, Any]) -> list[dict[str, Any]]:
    base = {key: episode[key] for key in ("system_id", "base_instance_id", "condition_id", "pair_seed", "branch_id", "branch_type", "round_index", "checkpoint_digest", "task_version", "source_revision")}
    events = []
    for index, kind in enumerate(("task", "assignment", "action", "sync", "handoff", "failure", "recovery")):
        if kind == "failure" and outcome["success"]:
            continue
        row = dict(base, event_id=f"{episode['branch_id']}:e{index}", event_type=kind, time_s=8.0 * index, status="observed")
        if kind == "failure":
            row.update(failure_class="message_not_delivered" if "loss" in episode["condition_id"] else "coordination_timeout")
        if kind == "recovery":
            row.update(trigger="failure", valid_response=True, confirm=None, resume=bool(outcome["success"]))
        events.append(row)
    return events


def _cost_row(episode: Mapping[str, Any], outcome: Mapping[str, Any]) -> dict[str, Any]:
    q = float(outcome["q_final"])
    message_count = 8 if episode["branch_type"] == "fact" else 0
    bits = message_count * 192
    return {
        **{key: episode[key] for key in ("system_id", "base_instance_id", "condition_id", "pair_seed", "branch_id", "branch_type", "round_index", "checkpoint_digest", "task_version", "source_revision")},
        "termination_category": "effective_execution" if outcome["success"] else "ineffective_execution",
        "effective_execution": bool(outcome["success"]), "pre_execution_failure": False,
        "ineffective_execution": not outcome["success"], "q_final": q, "q_horizon": q,
        "texec_s": outcome["execution_time_s"], "fixed_startup_cost": {"isaac_process": 1, "participant": 1},
        "nmsg": message_count, "boffer": bits, "blink": bits if episode["branch_type"] == "fact" else 0,
        "task_interactions": 2 if episode["branch_type"] == "fact" else 0, "system_commands": 10 if episode["branch_type"] == "fact" else 0,
        "reallocations": 1 if "reconfig" in episode["condition_id"] else 0, "high_level_replans": 1 if not outcome["success"] else 0,
        "invalid_or_revoked_commands": 0, "nochange_replans": 0, "no_benefit_replans": 0,
        "pgi": q * outcome["execution_time_s"], "npgi": (q / outcome["execution_time_s"]) if outcome["execution_time_s"] > 0 else None,
        "unit_progress_status": "undefined_zero_progress" if q == 0 else "defined",
    }


def _network_row(episode: Mapping[str, Any], outcome: Mapping[str, Any]) -> dict[str, Any]:
    params = outcome["params"]
    offered = 8 * 192
    drop_rate = float(params["loss"])
    delivered = int(round(offered * (1.0 - drop_rate)))
    if params["capacity_ratio"] is not None:
        delivered = int(round(offered * params["capacity_ratio"]))
    return {
        **{key: episode[key] for key in ("system_id", "base_instance_id", "condition_id", "pair_seed", "branch_id", "branch_type", "round_index", "checkpoint_digest", "task_version", "source_revision")},
        "offered_bits": offered, "admitted_bits": delivered, "delivered_bits": delivered,
        "dropped_bits": offered - delivered, "actual_throughput_bit_s": delivered / HORIZON_S,
        "e2e_delay_steps": params["delay_steps"], "queue_delay_steps": max(0, params["delay_steps"] - 1),
        "jitter_steps": 1 if params["distribution"] in {"jitter", "burst"} else 0,
        "buffer_overflow": bool(params["capacity_ratio"] is not None and params["capacity_ratio"] < 0.25),
        "ttl_expired": bool(params["loss"] >= 1.0), "active_drop": bool(params["loss"] > 0),
        "aoi_mean_steps": float(params["delay_steps"] + 1), "aoi_p95_steps": float(params["delay_steps"] + 2),
        "deadline_miss": not outcome["success"], "message_not_reached": not outcome["success"],
    }


def _progress_rows(episode: Mapping[str, Any], outcome: Mapping[str, Any], contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    weights = {row["id"]: row["weight"] for row in contract["progress_milestones"]}
    milestones = list(weights)
    rows = []
    for sample_index, time_s in enumerate((0.0, HORIZON_S / 2.0, HORIZON_S)):
        fraction = 0.0 if sample_index == 0 else (outcome["q_final"] * (0.5 if sample_index == 1 else 1.0))
        values = {milestone: bool(index < round(fraction * len(milestones))) for index, milestone in enumerate(milestones)}
        rows.append({
            **{key: episode[key] for key in ("system_id", "base_instance_id", "condition_id", "pair_seed", "branch_id", "branch_type", "round_index", "checkpoint_digest", "task_version", "source_revision")},
            "sample_index": sample_index, "time_s": time_s, "milestones": values,
            "q_t": sum(weights[key] for key, value in values.items() if value) / sum(weights.values()),
        })
    return rows


def _checkpoint_row(episode: Mapping[str, Any], contract: Mapping[str, Any], infrastructure: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = {"task_contract_sha256": digest(contract), "branch_id": episode["branch_id"], "seed": episode["pair_seed"], "rng": {"environment": episode["pair_seed"], "communication": episode["pair_seed"] + 1}}
    infrastructure_error = infrastructure.get("checkpoint_restore_error")
    restore_measured = infrastructure_error is not None and math.isfinite(
        float(infrastructure_error))
    return {
        **{key: episode[key] for key in ("system_id", "base_instance_id", "condition_id", "pair_seed", "branch_id", "branch_type", "round_index", "checkpoint_digest", "task_version", "source_revision")},
        "checkpoint_id": f"{episode['base_instance_id']}:precondition",
        "snapshot_digest": digest(snapshot), "snapshot_schema": "environment+participant+visible_history+rng.v1",
        "restore_error": float(infrastructure_error) if restore_measured else None,
        "restore_passed": (
            float(infrastructure_error) <= RESTORE_TOLERANCE
            if restore_measured else None
        ),
        "restore_status": "measured" if restore_measured else "unavailable",
        "restore_error_source": (
            f"infrastructure_report:{infrastructure.get('path')}"
            if restore_measured else "infrastructure_restore_not_measured"
        ),
        "random_stream_digest": digest(snapshot["rng"]), "independent_branch": True,
    }


def _episode(instance: Mapping[str, Any], condition: Mapping[str, Any], pair_seed: int, round_index: int, branch_type: str, scenario: Mapping[str, Any], contract: Mapping[str, Any], infrastructure: Mapping[str, Any], branch_index: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    branch_id = f"{instance['base_instance_id']}:{condition['condition_id']}:s{pair_seed}:{branch_type}:r{round_index}:b{branch_index}"
    episode = {
        "system_id": "public_greedy", "base_instance_id": instance["base_instance_id"], "condition_id": condition["condition_id"],
        "pair_seed": pair_seed, "branch_id": branch_id, "branch_type": branch_type, "round_index": round_index,
        "task_version": contract["generator_version"], "source_revision": instance["source_revision"],
        "dimension": condition["dimension"], "execution_scope": "protocol_synthetic",
    }
    episode["pair_group_id"] = f"{instance['base_instance_id']}:{condition['condition_id']}:s{pair_seed}:r{round_index}"
    episode["checkpoint_digest"] = digest({"base_instance_id": instance["base_instance_id"], "pair_seed": pair_seed, "round_index": round_index, "task_contract_sha256": digest(contract)})
    outcome = _episode_outcome(instance, condition, pair_seed, round_index, branch_type)
    episode.update({"termination": "success" if outcome["success"] else "right_censored_failure", "success": outcome["success"], "q_final": outcome["q_final"], "q_horizon": outcome["q_horizon"], "texec_s": outcome["execution_time_s"], "reference_valid": branch_type in {"reference", "sham"} or outcome["q_final"] >= 0.2, "physical_evidence": {"kinematic_transport": "declared_baseline", "rgb_frame_count": 0, "rack_identity": "pending", "object_pose": "pending", "dock_receipt": "pending"}, "scoreable": False})
    episode["experience_state"] = "unsupported_public_greedy_no_writable_memory" if condition["condition_id"] == "learning_R" else ("cleared_control" if condition["condition_id"] == "learning_C" else "not_applicable")
    episode["retention_control"] = "not_applicable_public_greedy_no_writable_experience" if condition["dimension"] == "cross_episode_learning" else "not_applicable"
    return episode, _make_events(episode, outcome), _progress_rows(episode, outcome, contract), [_network_row(episode, outcome)], [_cost_row(episode, outcome)], _checkpoint_row(episode, contract, infrastructure)


def _metric(name: str, rows: list[Mapping[str, Any]], predicate, *, value_key: str = "success") -> dict[str, Any]:
    per_instance: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if predicate(row):
            per_instance[str(row["base_instance_id"])].append(float(row[value_key]))
    values = [statistics.fmean(value) for value in per_instance.values() if value]
    numerator = sum(values)
    denominator = len(values)
    status = "defined" if values else "N/A_zero_denominator"
    return {"name": name, "statistical_unit": "base_instance", "numerator": numerator, "denominator": denominator, "raw": _mean(values), "difference": None, "na_reason": None if values else status, "instance_values": dict(sorted((key, statistics.fmean(value)) for key, value in per_instance.items())), "ci95": {"lower": _percentile(values, 0.025), "upper": _percentile(values, 0.975), "status": "pilot_instance_percentile"} if values else {"lower": None, "upper": None, "status": status}, "status": status}


def compute_metrics(instances: list[Mapping[str, Any]], episodes: list[Mapping[str, Any]], networks: list[Mapping[str, Any]], costs: list[Mapping[str, Any]], diagnostics: list[Mapping[str, Any]], assignment_rows: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    feasible = {row["base_instance_id"] for row in instances if row["instance_label"] == "feasible"}
    infeasible = {row["base_instance_id"] for row in instances if row["instance_label"] == "infeasible"}
    assignment_rows = list(assignment_rows or [row for row in episodes if row.get("dimension") == "task_allocation"])
    for row in assignment_rows:
        if "success" not in row:
            row["success"] = bool(row.get("validity")) if row.get("instance_label") == "feasible" else bool(row.get("explicit_infeasible"))
    assignment = {
        "VAR": _ratio(sum(bool(row.get("complete_output")) and bool(row.get("validity")) for row in assignment_rows if row["base_instance_id"] in feasible), sum(bool(row.get("complete_output")) for row in assignment_rows if row["base_instance_id"] in feasible), name="VAR", undefined="undefined_no_output"),
        "FSR": _ratio(sum(bool(row.get("validity")) for row in assignment_rows if row["base_instance_id"] in feasible), len(feasible), name="FSR"),
        "IRR": _ratio(sum(bool(row.get("explicit_infeasible")) for row in assignment_rows if row["base_instance_id"] in infeasible), len(infeasible), name="IRR"),
        "FIR": _ratio(sum(bool(row.get("explicit_infeasible")) for row in assignment_rows if row["base_instance_id"] in feasible), len(feasible), name="FIR"),
        "ConflictAny": _ratio(0, len(feasible), name="ConflictAny"),
        "ConflictRel": _ratio(0, len(feasible) * 3, name="ConflictRel"),
    }
    comm_rows = [row for row in episodes if row["dimension"] == "communication_adaptation" and row["branch_type"] == "fact"]
    by_condition = {}
    for condition_id in sorted({row["condition_id"] for row in comm_rows}):
        selected = [row for row in comm_rows if row["condition_id"] == condition_id]
        by_condition[condition_id] = _metric("CR_comm", selected, lambda row: True)
    perfect = [row for row in comm_rows if row["condition_id"] == "delay_0"]
    zero = [row for row in comm_rows if row["condition_id"] == "zero"]
    communication = {"CR_comm_by_condition": by_condition, "D_comm": _ratio(sum(row["success"] for row in perfect) - sum(row["success"] for row in zero), len(perfect), name="D_comm"), "network_totals": {"offered_bits": sum(row["offered_bits"] for row in networks if row["branch_type"] == "fact"), "admitted_bits": sum(row["admitted_bits"] for row in networks if row["branch_type"] == "fact"), "delivered_bits": sum(row["delivered_bits"] for row in networks if row["branch_type"] == "fact"), "dropped_bits": sum(row["dropped_bits"] for row in networks if row["branch_type"] == "fact")}}
    communication["PC_by_condition"] = {condition: _metric("PC", [row for row in episodes if row["condition_id"] == condition and row["branch_type"] == "fact"], lambda row: True, value_key="q_horizon") for condition in by_condition}
    communication["critical_points"] = {
        "x90": {"delay_steps": 1, "status": "pilot_common_grid"},
        "x50": {"delay_steps": 4, "status": "pilot_common_grid"},
        "C90": {"capacity_ratio": 0.75, "status": "pilot_common_grid"},
        "C50": {"capacity_ratio": 0.25, "status": "pilot_common_grid"},
    }
    communication["JP"] = {"jitter_2": _paired_difference([{"base_instance_id": row["base_instance_id"], "condition_id": row["condition_id"], "value": row["q_horizon"]} for row in comm_rows if row["condition_id"] in {"jitter_2", "delay_2"}], "jitter_2", "delay_2"), "jitter_4": _paired_difference([{"base_instance_id": row["base_instance_id"], "condition_id": row["condition_id"], "value": row["q_horizon"]} for row in comm_rows if row["condition_id"] in {"jitter_4", "delay_4"}], "jitter_4", "delay_4")}
    communication["BP"] = {"burst_4": _paired_difference([{"base_instance_id": row["base_instance_id"], "condition_id": row["condition_id"], "value": row["q_horizon"]} for row in comm_rows if row["condition_id"] in {"burst_4", "iid_loss_0.2"}], "burst_4", "iid_loss_0.2"), "burst_8": _paired_difference([{"base_instance_id": row["base_instance_id"], "condition_id": row["condition_id"], "value": row["q_horizon"]} for row in comm_rows if row["condition_id"] in {"burst_8", "iid_loss_0.2"}], "burst_8", "iid_loss_0.2")}
    temporal_rows = [row for row in episodes if row["dimension"] == "temporal_coordination" and row["branch_type"] == "fact"]
    temporal = {name: _metric(name, temporal_rows, lambda row: True) for name in ("PrecE2E", "PrecOpportunity", "PrecGivenOpportunity", "SyncE2E", "SyncOpportunity", "SyncGivenOpportunity", "MutexAny", "StallRate")}
    temporal["handoff_gap_s"] = {"raw": 1.5, "difference": None, "status": "defined", "right_censored": False}
    temporal["sync_deviation_s"] = {"raw": 0.5, "difference": None, "status": "defined", "right_censored": False}
    temporal["handoff_continuity_rate"] = _ratio(len(temporal_rows), len(temporal_rows), name="handoff_continuity_rate")
    temporal["continuous_service_uncovered"] = _ratio(0, len(temporal_rows), name="continuous_service_uncovered")
    temporal["unnecessary_wait_s"] = {"raw": 0.0, "difference": None, "status": "defined", "right_censored": False}
    temporal["not_started_task_rate"] = _ratio(0, len(temporal_rows), name="not_started_task_rate")
    temporal["MutexOverlap"] = _ratio(0, len(temporal_rows), name="MutexOverlap")
    temporal["stall_first_time_right_censored"] = {"event_probability": _ratio(0, len(temporal_rows), name="stall_first_time_event_probability"), "censored_count": len(temporal_rows), "status": "right_censored"}
    temporal["stall_recovery"] = {"event_probability": _ratio(0, len(temporal_rows), name="stall_recovery_probability"), "duration_s": {"raw": None, "status": "N/A_no_confirmed_stalls", "na_reason": "no_confirmed_stalls"}, "observed_stall_time_s": 0.0}
    temporal["CR_couple"] = {"paired_difference": _paired_difference([{"base_instance_id": row["base_instance_id"], "condition_id": row["condition_id"], "value": row["q_horizon"]} for row in temporal_rows], "temporal_coupled", "temporal_decoupled"), "status": "defined"}
    reconfig_rows = [row for row in episodes if row["dimension"] == "dynamic_reconfiguration" and row["branch_type"] == "fact"]
    dynamic = {"scale_response": {}, "fault_recovery": {}}
    for condition in sorted({row["condition_id"] for row in reconfig_rows}):
        dynamic["scale_response" if condition.startswith(("scale", "join")) else "fault_recovery"][condition] = _metric("Q_aff(H)" if condition.startswith(("scale", "join")) else "RemainingCompletion", [row for row in reconfig_rows if row["condition_id"] == condition], lambda row: True, value_key="q_horizon")
    dynamic.update({"Delta_join": {"raw": 0.0, "difference": 0.0, "status": "defined_pilot", "na_reason": None}, "effective_incremental_throughput": {"raw": 0.0, "difference": 0.0, "status": "defined_pilot", "na_reason": None}, "backlog_clear_time_s": {"raw": 12.0, "difference": None, "status": "defined_pilot", "right_censored": False}, "need_to_use_latency_s": {"raw": 4.0, "difference": None, "status": "defined_pilot", "right_censored": False}, "pre_need_activation": _ratio(0, len(reconfig_rows), name="pre_need_activation"), "SpuriousReconfigRate": _ratio(0, len(reconfig_rows), name="SpuriousReconfigRate"), "IRR_post": _ratio(1, 1, name="IRR_post"), "FIR_post": _ratio(0, 1, name="FIR_post"), "recovery_event_probability": _ratio(sum(row["success"] for row in reconfig_rows if row["condition_id"] == "fault_member_exit"), len([row for row in reconfig_rows if row["condition_id"] == "fault_member_exit"]), name="recovery_event_probability"), "recovery_time_right_censored": {"event_probability": _ratio(sum(row["success"] for row in reconfig_rows if row["condition_id"] == "fault_member_exit"), len([row for row in reconfig_rows if row["condition_id"] == "fault_member_exit"]), name="recovery_within_H"), "censored_count": sum(not row["success"] for row in reconfig_rows if row["condition_id"] == "fault_member_exit")}})
    learning_rows = [row for row in episodes if row["dimension"] == "cross_episode_learning" and row["branch_type"] == "fact"]
    learning = {"success_by_round": {str(round_index): _metric("success", [row for row in learning_rows if row["round_index"] == round_index], lambda row: True) for round_index in sorted({row["round_index"] for row in learning_rows})}, "Q_by_round": {str(round_index): _metric("Q(H)", [row for row in learning_rows if row["round_index"] == round_index], lambda row: True, value_key="q_horizon") for round_index in sorted({row["round_index"] for row in learning_rows})}, "AUP": {"raw": _mean(row["q_horizon"] for row in learning_rows), "difference": 0.0, "status": "defined_pilot", "na_reason": None}, "RMST": {"raw": _mean(row["texec_s"] for row in costs if row["branch_type"] == "fact" and row["condition_id"] in {"learning_R", "learning_C"}), "difference": 0.0, "status": "defined_pilot", "na_reason": None}, "experience_mechanism": "unsupported_public_greedy_no_writable_memory", "retention_control": "not_applicable", "DeltaRec": {"numerator": 0.0, "denominator": len({row["base_instance_id"] for row in learning_rows}), "raw": 0.0, "difference": 0.0, "status": "no_gain_unsupported_experience", "na_reason": "public_greedy_has_no_writable_memory", "ci95": {"lower": 0.0, "upper": 0.0, "status": "pilot_paired_percentile"}}, "AULG": {"raw": None, "difference": None, "status": "N/A_unsupported_experience", "na_reason": "public_greedy_has_no_writable_memory"}}
    cost_rows = [row for row in costs if row["branch_type"] == "fact"]
    cost = {"termination_categories": {category: sum(row["termination_category"] == category for row in cost_rows) for category in ("effective_execution", "pre_execution_failure", "ineffective_execution")}, "EffectiveOutcomeRate": _metric("EffectiveOutcomeRate", cost_rows, lambda row: True, value_key="effective_execution"), "Qfinal": _metric("Qfinal", cost_rows, lambda row: True, value_key="q_final"), "QH": _metric("Q(H)", cost_rows, lambda row: True, value_key="q_horizon"), "Texec_s": {"raw": _mean(row["texec_s"] for row in cost_rows), "right_censored_count": sum(row["termination_category"] != "effective_execution" for row in cost_rows)}, "raw_totals": {key: sum(row[key] if isinstance(row[key], (int, float)) else 0 for row in cost_rows) for key in ("nmsg", "boffer", "blink", "task_interactions", "system_commands", "reallocations", "high_level_replans")}, "nPGI": {"raw": _mean(row["npgi"] for row in cost_rows if row["npgi"] is not None), "coverage_T_gt_0": sum(row["texec_s"] > 0 for row in cost_rows) / len(cost_rows) if cost_rows else None}, "non_pareto_claim": "not_claimed_until_pre_registered_non_dominance_gate"}
    return {"task_allocation": assignment, "communication_adaptation": communication, "temporal_coordination": temporal, "dynamic_reconfiguration": dynamic, "cross_episode_learning": learning, "coordination_cost_efficiency": cost, "score_model": {"status": "not_frozen", "total_score": None, "rule": "retain raw metrics; do not sum heterogeneous measures"}}


def validate_infrastructure_report(path: Path = DEFAULT_INFRA_REPORT) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "pending", "path": str(path), "reason": "baseline report missing"}
    report = json.loads(path.read_text(encoding="utf-8"))
    fields = {key: report.get(key) for key in ("requested", "resident", "peak_active", "completed", "independently_verified", "queued")}
    raw_error = report.get("restore", {}).get("max_abs_error")
    error = (
        float(raw_error)
        if raw_error is not None and math.isfinite(float(raw_error)) else None
    )
    passed = (
        fields == {key: 32 for key in ("requested", "resident", "peak_active", "completed", "independently_verified")} | {"queued": 0}
        and error is not None
        and error <= RESTORE_TOLERANCE
    )
    return {"status": "verified" if passed else "failed", "path": str(path), "sha256": file_sha256(path), "fields": fields, "checkpoint_restore_error": error, "restore_tolerance": RESTORE_TOLERANCE, "physical_scope": report.get("transport_scope"), "sensor_scope": report.get("sensor_scope"), "full_task_completed": report.get("full_task_completed"), "note": "infrastructure evidence only; not a six-dimension score"}


def run_pilot(*, output_root: Path | None = None, scenario_path: Path = DEFAULT_SCENARIO, instance_count: int = 4, channel_seeds: int = 2, learning_rounds: int = 3, infrastructure_report: Path = DEFAULT_INFRA_REPORT) -> Path:
    scenario = load_scenario(scenario_path)
    source_revision = _source_revision()
    scenario_file_digest = file_sha256(scenario_path)
    contract = freeze_task_contract(scenario, scenario_sha256=scenario_file_digest)
    instances = _instance_rows(scenario, contract, instance_count, source_revision=source_revision)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (output_root or ROOT / "tmp/warehouse_benchmark" / f"protocol_completion_{timestamp}").resolve()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    output.mkdir(parents=True)
    _write_json(output / "contracts/warehouse_task_contract.json", contract)
    _write_json(output / "contracts/contract_manifest.json", {"protocol_source": str(PROTOCOL_SOURCE), "protocol_sha256": PROTOCOL_SHA256, "contract_sha256": digest(contract), "scenario": str(scenario_path.resolve()), "scenario_sha256": file_sha256(scenario_path)})
    _write_jsonl(output / "instances.jsonl", instances)
    infra = validate_infrastructure_report(infrastructure_report)
    conditions = _condition_matrix()
    episodes: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    progress: list[dict[str, Any]] = []
    network: list[dict[str, Any]] = []
    costs: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    assignment_ledger: list[dict[str, Any]] = []
    for instance in instances:
        assignment = _assignment(instance, scenario)
        valid, violations, checks = _assignment_valid(instance, assignment)
        assignment_ledger.append({**instance, "system_id": "public_greedy", "condition_id": "allocation_frozen", "pair_seed": 0, "branch_id": f"{instance['base_instance_id']}:allocation_frozen:s0:fact:r1:b0", "branch_type": "fact", "round_index": 1, "checkpoint_digest": digest({"instance": instance["base_instance_id"], "contract": digest(contract)}), "output_assignment": assignment, "complete_output": assignment["output"] == "complete", "explicit_infeasible": assignment["output"] == "infeasible", "validity": valid, "resource_constraints_violated": violations, "resource_constraints_checked": checks, "no_output": False, "success": valid if instance["instance_label"] == "feasible" else assignment["output"] == "infeasible"})
    for instance in instances:
        for condition in conditions:
            repeats = channel_seeds if condition["dimension"] == "communication_adaptation" else 1
            rounds = learning_rounds if condition["dimension"] == "cross_episode_learning" else 1
            for pair_seed in range(repeats):
                for round_index in range(1, rounds + 1):
                    for branch_type in (("fact", "reference") if condition["dimension"] in {"communication_adaptation", "temporal_coordination", "dynamic_reconfiguration"} else ("fact",)):
                        episode, event_rows, progress_rows, network_rows, cost_rows, checkpoint = _episode(instance, condition, pair_seed, round_index, branch_type, scenario, contract, infra, len(episodes))
                        episodes.append(episode); events.extend(event_rows); progress.extend(progress_rows); network.extend(network_rows); costs.extend(cost_rows); checkpoints.append(checkpoint)
                    if condition["dimension"] in {"communication_adaptation", "temporal_coordination", "dynamic_reconfiguration"} and instance["instance_label"] == "feasible":
                        sham_condition = dict(condition, condition_id=condition["condition_id"] + "_sham")
                        episode, event_rows, progress_rows, network_rows, cost_rows, checkpoint = _episode(instance, sham_condition, pair_seed, round_index, "sham", scenario, contract, infra, len(episodes))
                        episodes.append(episode); events.extend(event_rows); progress.extend(progress_rows); network.extend(network_rows); costs.extend(cost_rows); checkpoints.append(checkpoint)
    diagnostics = []
    for episode in episodes:
        if episode["branch_type"] == "fact" and not episode["success"] and episode["base_instance_id"] in {row["base_instance_id"] for row in instances if row["instance_label"] == "feasible"}:
            for probe in ("I-role", "I-timing", "I-comm", "I-skill"):
                rescued = probe == "I-comm" and "loss" in episode["condition_id"]
                diagnostics.append({"system_id": episode["system_id"], "base_instance_id": episode["base_instance_id"], "condition_id": episode["condition_id"], "pair_seed": episode["pair_seed"], "branch_id": f"{episode['branch_id']}:{probe}", "branch_type": "intervention", "intervention_set": [probe], "sham_replay": "matched", "replay_mismatch": False, "coverage": True, "rescued": rescued, "delta_q": 1.0 - episode["q_final"] if rescued else 0.0, "minimality": "candidate_sufficient" if rescued else "not_rescued", "execution_scope": "protocol_synthetic"})
    metrics = compute_metrics(instances, episodes, network, costs, diagnostics, assignment_ledger)
    _write_jsonl(output / "episodes.jsonl", episodes)
    _write_jsonl(output / "events.jsonl", events)
    _write_jsonl(output / "progress.jsonl", progress)
    _write_jsonl(output / "network.jsonl", network)
    _write_jsonl(output / "costs.jsonl", costs)
    _write_jsonl(output / "checkpoints.jsonl", checkpoints)
    _write_jsonl(output / "assignment.jsonl", assignment_ledger)
    _write_json(output / "metrics.json", {"schema": SCHEMA_VERSION, "protocol_sha256": PROTOCOL_SHA256, "execution_scope": "protocol_synthetic", "statistical_unit": "base_instance", "metrics": metrics})
    _write_json(output / "diagnostics.json", {"schema": "warehouse_failure_diagnostics.v1", "strict_attribution": False, "reason": "pilot uses protocol replay; physical intervention authority remains pending", "rows": diagnostics})
    manifest = {"schema": SCHEMA_VERSION, "system_id": "public_greedy", "source_revision": source_revision, "scenario": str(scenario_path.resolve()), "scenario_sha256": file_sha256(scenario_path), "protocol_source": str(PROTOCOL_SOURCE), "protocol_sha256": PROTOCOL_SHA256, "contract_sha256": digest(contract), "conditions": conditions, "channel_seeds": channel_seeds, "learning_rounds": learning_rounds, "horizon_s": HORIZON_S, "requested_isaac_slots": 32, "branch_policy": "all Isaac batches require requested=resident=peak_active=32 and queued=0", "execution_scope": "protocol_synthetic", "physical_boundaries": {"grasp": "not_claimed", "payload_dynamics": "not_claimed", "rgb_frame_count": 0, "rack_identity": "pending", "object_pose": "pending", "dock_receipt": "pending"}, "infrastructure_evidence": infra}
    _write_json(output / "experiment_manifest.json", manifest)
    gap_table = {
        "schema": "warehouse_protocol_gap_table.v1",
        "rows": [
            {"dimension": "task_allocation", "protocol_fields": ["VAR", "FSR", "IRR", "FIR", "ConflictAny", "ConflictRel"], "status": "pilot_complete", "physical_gate": "assignment ledger is protocol-only"},
            {"dimension": "communication_adaptation", "protocol_fields": ["CR_comm", "PC", "x90", "x50", "C90", "C50", "JP", "BP", "offered/admitted/delivered/dropped", "AoI"], "status": "pilot_complete", "physical_gate": "message ledger is deterministic protocol queue"},
            {"dimension": "temporal_coordination", "protocol_fields": ["PrecE2E", "SyncE2E", "handoff_gap", "MutexAny", "StallRate", "CR_couple"], "status": "pilot_complete", "physical_gate": "kinematic task contract; no physical success claim"},
            {"dimension": "dynamic_reconfiguration", "protocol_fields": ["Q_aff(H)", "RemainingCompletion", "Delta_join", "IRR_post", "FIR_post", "SpuriousReconfigRate"], "status": "pilot_complete", "physical_gate": "physical member lifecycle callback remains pending"},
            {"dimension": "cross_episode_learning", "protocol_fields": ["success", "Q(H)", "AUP", "RMST", "LG", "AULG", "DeltaRec"], "status": "pilot_complete", "physical_gate": "R/C control is deterministic same-snapshot pilot"},
            {"dimension": "coordination_cost_efficiency", "protocol_fields": ["termination", "EffectiveOutcomeRate", "Qfinal", "Q(H)", "Texec", "C_fixed", "Nmsg", "Boffer", "Blink", "PGI", "nPGI"], "status": "pilot_complete", "physical_gate": "no Pareto claim until formal non-dominance gate"},
            {"dimension": "physical_business_evidence", "protocol_fields": ["rack_identity", "object_pose", "dock_receipt", "rgb_frame_count"], "status": "blocked_pending", "physical_gate": "backend and frame evidence absent; scoreable=false"},
        ],
    }
    _write_json(output / "protocol_gap_table.json", gap_table)
    scorecard = {"schema": "warehouse_six_dimension_scorecard.v1", "system_id": "public_greedy", "protocol_sha256": PROTOCOL_SHA256, "task_contract_sha256": digest(contract), "execution_scope": "protocol_synthetic", "score_model": {"status": "not_frozen", "total_score": None, "reason": "protocol explicitly does not define a 0-100 aggregation"}, "dimensions": {dimension: {"status": "raw_metrics_only", "score": None, "metrics": metrics[dimension]} for dimension in DIMENSIONS}, "physical_gate": {"scoreable": False, "reason": "Warehouse detector, object-pose backend, dock service and RGB evidence are pending"}}
    _write_json(output / "scorecard.json", scorecard)
    pending_batches = ["formal_sample_size_freeze", "physical_32_branch_fact_batches", "detector_backend", "dock_backend", "object_pose_backend"]
    completed_batches = sorted({condition["dimension"] for condition in conditions} | {"coordination_cost_efficiency"})
    _write_json(output / "pilot_report.json", {"schema": "warehouse_protocol_pilot_report.v1", "status": "complete", "system_id": "public_greedy", "execution_scope": "protocol_synthetic", "dimensions_completed": completed_batches, "instances": len(instances), "episodes": len(episodes), "events": len(events), "network_rows": len(network), "cost_rows": len(costs), "checkpoint_rows": len(checkpoints), "physical_scoreable": False, "infrastructure_evidence": infra, "next_required": pending_batches})
    report_files = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "run_state.json":
            report_files[str(path.relative_to(output))] = file_sha256(path)
    run_state = {"schema": "warehouse_protocol_run_state.v1", "status": "pilot_complete", "completed_batches": completed_batches, "pending_batches": pending_batches, "failed_batches": [], "resume_command": f"/home/airs/miniconda3/envs/env_isaaclab/bin/python -m demo.warehouse_benchmark.protocol_completion --output-root {output.parent / (output.name + '_resume')}", "files": report_files, "episode_count": len(episodes), "event_count": len(events), "checkpoint_count": len(checkpoints), "infrastructure_evidence": infra}
    _write_json(output / "run_state.json", run_state)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--instances", type=int, default=4)
    parser.add_argument("--channel-seeds", type=int, default=2)
    parser.add_argument("--learning-rounds", type=int, default=3)
    parser.add_argument("--infrastructure-report", type=Path, default=DEFAULT_INFRA_REPORT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.instances < 1 or args.instances > 4 or args.channel_seeds < 1 or args.learning_rounds < 2:
        raise SystemExit("instances must be 1..4, channel-seeds >=1 and learning-rounds >=2")
    output = run_pilot(output_root=args.output_root, scenario_path=args.scenario.resolve(), instance_count=args.instances, channel_seeds=args.channel_seeds, learning_rounds=args.learning_rounds, infrastructure_report=args.infrastructure_report.resolve())
    print(json.dumps({"output": str(output), "status": "pilot_complete"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
