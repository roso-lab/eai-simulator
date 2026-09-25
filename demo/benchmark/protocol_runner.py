"""Portable six-dimension benchmark protocol runner.

The runner is scene agnostic.  A scene supplies a JSON task contract through
``SceneProtocolAdapter`` and a participant supplies task allocation through
``ProtocolParticipant``.  ``GenericJsonSceneAdapter`` covers the repository's
phase-graph JSON scenarios, while projects with richer domain semantics can
implement the same adapter interface without changing the ledgers, pairing,
statistics or scorecard code.

This is a protocol pilot and does not start Isaac Sim.  Physical execution is
an adapter-owned evidence source and remains explicitly pending unless an
adapter supplies it.  The same API is intended to be used by a future 32-slot
Isaac executor; the runner rejects no slot count, but records the required
32-slot gate in the manifest whenever physical batches are requested.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
from typing import Any, Iterable, Mapping, Protocol, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO = ROOT / "demo/warehouse_benchmark/scenarios/business_logistics_01.json"
DEFAULT_HORIZON_S = 120.0
DEFAULT_PROTOCOL_SHA256 = "539822d71b46b520c21df9170b182e0b7fe66016b8cfb26825a33294f52651c1"
SCHEMA_VERSION = "eai_protocol_runner.v1"
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
REQUIRED_RECORD_KEYS = {
    "system_id", "base_instance_id", "condition_id", "pair_seed", "branch_id",
    "branch_type", "round_index", "checkpoint_digest", "task_version",
    "source_revision",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _source_revision(root: Path = ROOT) -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def _stable_unit(*parts: Any) -> float:
    value = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).digest()
    return int.from_bytes(value[:8], "big") % 1_000_000 / 1_000_000.0


def wilson_ci(numerator: int, denominator: int, z: float = 1.959963984540054) -> dict[str, Any]:
    if denominator == 0:
        return {"lower": None, "upper": None, "status": "N/A_zero_denominator"}
    p = numerator / denominator
    centre = (p + z * z / (2 * denominator)) / (1 + z * z / denominator)
    half = z * math.sqrt(p * (1 - p) / denominator + z * z / (4 * denominator * denominator)) / (1 + z * z / denominator)
    return {"lower": max(0.0, centre - half), "upper": min(1.0, centre + half), "status": "defined"}


def _ratio(name: str, numerator: float, denominator: float, *, na_reason: str = "N/A_zero_denominator") -> dict[str, Any]:
    if denominator == 0:
        return {"name": name, "numerator": numerator, "denominator": denominator, "raw": None, "difference": None, "status": na_reason, "na_reason": na_reason, "ci95": wilson_ci(0, 0)}
    raw = numerator / denominator
    ci = wilson_ci(int(numerator), int(denominator)) if float(numerator).is_integer() and float(denominator).is_integer() else {"lower": None, "upper": None, "status": "not_binary"}
    return {"name": name, "numerator": numerator, "denominator": denominator, "raw": raw, "difference": None, "status": "defined", "na_reason": None, "ci95": ci}


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return statistics.fmean(values) if values else None


def _percentile(values: Iterable[float], q: float) -> float | None:
    values = sorted(float(value) for value in values)
    if not values:
        return None
    index = (len(values) - 1) * q
    low, high = math.floor(index), math.ceil(index)
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (index - low)


def paired_difference(rows: Iterable[Mapping[str, Any]], left: str, right: str) -> dict[str, Any]:
    grouped: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        grouped[str(row["base_instance_id"])][str(row["condition_id"])] = float(row["value"])
    differences = [value[left] - value[right] for value in grouped.values() if left in value and right in value]
    if not differences:
        return {"numerator": 0, "denominator": 0, "raw": None, "difference": None, "status": "N/A_no_pairs", "na_reason": "N/A_no_pairs", "ci95": {"lower": None, "upper": None, "status": "N/A_no_pairs"}}
    mean = statistics.fmean(differences)
    return {"numerator": sum(differences), "denominator": len(differences), "raw": mean, "difference": mean, "status": "defined", "na_reason": None, "ci95": {"lower": _percentile(differences, 0.025), "upper": _percentile(differences, 0.975), "status": "pilot_paired_percentile"}}


class ProtocolParticipant(Protocol):
    """Replaceable high-level participant boundary."""

    participant_id: str

    @property
    def experience_support(self) -> str: ...

    def assign(self, scenario: Mapping[str, Any], instance: Mapping[str, Any], nodes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]: ...


class SceneProtocolAdapter(Protocol):
    """Scene-owned contract and domain evidence boundary."""

    scene_key: str

    def load_scenario(self, path: Path) -> Mapping[str, Any]: ...

    def build_contract(self, scenario: Mapping[str, Any], *, scenario_sha256: str, protocol_sha256: str, horizon_s: float) -> Mapping[str, Any]: ...

    def build_instances(self, scenario: Mapping[str, Any], contract: Mapping[str, Any], *, count: int, source_revision: str) -> list[dict[str, Any]]: ...

    def validate_assignment(self, instance: Mapping[str, Any], assignment: Mapping[str, Any], scenario: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def physical_evidence(self, scenario: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def infrastructure_evidence(self) -> Mapping[str, Any]: ...


class PublicGreedyParticipant:
    participant_id = "public_greedy"
    experience_support = "unsupported_public_greedy_no_writable_memory"

    def assign(self, scenario: Mapping[str, Any], instance: Mapping[str, Any], nodes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        if instance["instance_label"] != "feasible":
            return {"output": "infeasible", "assignments": {}, "declaration_reason": instance.get("infeasible_reason", "frozen feasibility certificate")}
        return {"output": "complete", "assignments": {str(node["id"]): list(node.get("agents") or [node.get("preferred_agent")]) for node in nodes}, "declaration_reason": None}


@dataclass
class GenericJsonSceneAdapter:
    """Default adapter for any phase-graph JSON scene."""

    scene_key: str | None = None
    scenario_path: Path | None = None
    infrastructure_report: Path | None = None

    def load_scenario(self, path: Path) -> Mapping[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not str(data.get("scene_key", "")).strip():
            raise ValueError("scenario must be an object with a nonempty scene_key")
        nodes = data.get("phase_graph", {}).get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("scenario.phase_graph.nodes must be a nonempty list")
        ids = [node.get("id") for node in nodes]
        if any(not isinstance(identifier, str) or not identifier for identifier in ids) or len(ids) != len(set(ids)):
            raise ValueError("phase graph node ids must be unique nonempty strings")
        if self.scene_key is not None and data["scene_key"] != self.scene_key:
            raise ValueError(f"adapter expects scene_key={self.scene_key!r}, got {data['scene_key']!r}")
        self.scene_key = str(data["scene_key"])
        self.scenario_path = path.resolve()
        return data

    @staticmethod
    def _nodes(scenario: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return list(scenario["phase_graph"]["nodes"])

    def build_contract(self, scenario: Mapping[str, Any], *, scenario_sha256: str, protocol_sha256: str, horizon_s: float) -> Mapping[str, Any]:
        nodes = self._nodes(scenario)
        robots = list(scenario.get("robots") or [])
        robot_by_id = {robot.get("id"): robot for robot in robots}
        node_ids = [str(node["id"]) for node in nodes]
        resources = sorted({str(resource) for node in nodes for resource in node.get("resources", [])})
        relations: list[dict[str, Any]] = []
        for node in nodes:
            for prerequisite in node.get("prerequisites", []):
                relations.append({"kind": "precedence", "from": prerequisite, "to": node["id"], "hard": True})
            members = list(node.get("agents") or [])
            if len(members) > 1:
                relations.append({"kind": "synchronization", "node": node["id"], "members": members, "window_s": float(node.get("sync_window_s", 2.0)), "hard": True})
            if node.get("object_ref"):
                relations.append({"kind": "handoff", "node": node["id"], "object": node["object_ref"], "max_gap_s": float(node.get("handoff_gap_s", 5.0)), "hard": True})
        relations.extend({"kind": "mutual_exclusion", "resource": resource, "capacity": 1, "hard": True} for resource in resources)
        milestones = [{"id": f"node:{node_id}", "weight": 2.0 if len(next(node for node in nodes if node["id"] == node_id).get("agents") or []) > 1 else 1.0, "predicate": f"{node_id} externally validated complete"} for node_id in node_ids]
        required_capabilities: dict[str, list[str]] = defaultdict(list)
        for node in nodes:
            preferred = node.get("preferred_agent")
            robot = robot_by_id.get(preferred, {})
            capability = robot.get("type", preferred or node.get("skill_key", "unknown"))
            required_capabilities[str(node.get("skill_key", "task"))].append(str(capability))
        acceptance = list((scenario.get("semantic_task") or {}).get("objective_semantics", {}).get("acceptance_evidence", []))
        return {
            "schema": "eai_task_contract.v1", "scene_key": scenario["scene_key"],
            "task_id": scenario.get("benchmark_task_id", scenario.get("task_name", "task")),
            "generator_version": f"{scenario.get('scene_key')}:json-phase-graph-v1",
            "scenario_sha256": scenario_sha256, "protocol_sha256": protocol_sha256,
            "initial_state": {"seed": scenario.get("seed"), "robots": deepcopy(robots), "entities": deepcopy((scenario.get("composition") or {}).get("entities", []))},
            "goal_state": {"all_phase_nodes_complete": True, "external_evidence": acceptance, "external_evidence_status": "pending" if acceptance else "not_declared"},
            "phase_graph": {"nodes": deepcopy(nodes), "relations": relations},
            "hard_constraints": [relation for relation in relations if relation.get("hard")],
            "soft_constraints": deepcopy(scenario.get("protocol_soft_constraints") or []),
            "tolerances": deepcopy(scenario.get("protocol_tolerances") or {}),
            "success_predicate": {"all_nodes": node_ids, "stable_duration_s": float(scenario.get("success_stable_duration_s", 3.0)), "external_evidence": acceptance, "external_evidence_status": "pending" if acceptance else "not_declared"},
            "progress_milestones": milestones,
            "minimum_team_certificate": {"feasible_team": [robot.get("id") for robot in robots], "minimum_team_size": len(robots), "required_capabilities": {key: sorted(set(value)) for key, value in required_capabilities.items()}, "delete_certificates": [{"removed": robot.get("id"), "status": "infeasible", "reason": "declared provider removed"} for robot in robots]},
            "target": "condition declared in experiment manifest", "nuisance": ["scene_geometry", "low_level_controller", "task_graph", "seed_tape", "horizon_s"],
            "failure_events": ["allocation_invalid", "message_not_delivered", "precedence_violation", "sync_miss", "resource_conflict", "member_failure", "recovery_timeout", "zero_progress"],
            "oracle": {"feasibility": "adapter minimum_team_certificate", "reference": "same checkpoint condition", "probe": "branch-and-bound sham/intervention"},
            "intervention_points": ["after first completed node", "before first coupled node", "after registered fault"],
            "snapshot_schema": {"environment": ["phase_state", "task_state", "external_noise_counter"], "participant": ["assignment", "memory", "message_history"], "rng": ["environment", "communication", "participant"]},
            "reference_validity": {"minimum_reference_success": 0.2, "minimum_reference_efficiency": 1e-4, "relative_metrics": "N/A below threshold"},
            "horizon_s": horizon_s, "n_a_pilot": {"pilot": 4, "formal": None, "freeze_rule": "freeze after CI width or MDE pilot review"},
        }

    def build_instances(self, scenario: Mapping[str, Any], contract: Mapping[str, Any], *, count: int, source_revision: str) -> list[dict[str, Any]]:
        labels = [
            ("feasible", "normal", "declared team is feasible", False),
            ("feasible", "resource_scarce", "feasible with an exclusive resource", False),
            ("infeasible", "capability_insufficient", "required provider removed", True),
            ("infeasible", "resource_conflict", "exclusive resource unavailable", True),
        ]
        prefix = str(contract["task_id"]).replace(" ", "_")
        rows = []
        for index, (label, stratum, reason, oracle_infeasible) in enumerate(labels[: max(1, min(count, len(labels)))]):
            rows.append({"base_instance_id": f"{prefix}-I{index + 1:02d}", "task_id": contract["task_id"], "task_version": contract["generator_version"], "instance_label": label, "stratum": stratum, "certificate": reason, "infeasible_reason": reason, "oracle_infeasible": oracle_infeasible, "seed": int(scenario.get("seed", 0)) + index, "initial_state": deepcopy(contract["initial_state"]), "goal_state": deepcopy(contract["goal_state"]), "phase_graph_sha256": sha256_value(contract["phase_graph"]), "task_contract_sha256": sha256_value(contract), "source_revision": source_revision, "scenario_sha256": contract["scenario_sha256"], "validity": "valid", "execution_scope": "protocol_synthetic"})
        return rows

    def validate_assignment(self, instance: Mapping[str, Any], assignment: Mapping[str, Any], scenario: Mapping[str, Any]) -> Mapping[str, Any]:
        nodes = self._nodes(scenario)
        node_ids = {str(node["id"]) for node in nodes}
        robot_ids = {str(robot["id"]) for robot in scenario.get("robots", [])}
        assignments = assignment.get("assignments") if isinstance(assignment, Mapping) else None
        checks = len({resource for node in nodes for resource in node.get("resources", [])})
        if instance["instance_label"] != "feasible":
            return {"valid": False, "violations": 0, "checks": checks, "reason": "instance is oracle-infeasible"}
        violations = 0
        if assignment.get("output") != "complete" or not isinstance(assignments, Mapping) or set(assignments) != node_ids:
            violations += 1
        if isinstance(assignments, Mapping):
            for node_id, members in assignments.items():
                if node_id not in node_ids or not isinstance(members, list) or not members or any(member not in robot_ids for member in members) or len(set(members)) != len(members):
                    violations += 1
        return {"valid": violations == 0, "violations": violations, "checks": checks, "reason": None if violations == 0 else "assignment does not satisfy the frozen node/robot contract"}

    def physical_evidence(self, scenario: Mapping[str, Any]) -> Mapping[str, Any]:
        custom = scenario.get("protocol_physical_evidence")
        if isinstance(custom, Mapping):
            return deepcopy(dict(custom))
        return {"scene_key": scenario["scene_key"], "execution_scope": "protocol_synthetic", "scoreable": False, "grasp": "pending", "payload_dynamics": "pending", "sensor_backend": "pending", "external_domain_evidence": "pending"}

    def infrastructure_evidence(self) -> Mapping[str, Any]:
        if self.infrastructure_report is None or not self.infrastructure_report.is_file():
            return {"status": "not_supplied", "physical_batch": False}
        report = json.loads(self.infrastructure_report.read_text(encoding="utf-8"))
        fields = {key: report.get(key) for key in ("requested", "resident", "peak_active", "completed", "independently_verified", "queued")}
        raw_restore_error = (report.get("restore") or {}).get("max_abs_error")
        restore_error = (
            float(raw_restore_error)
            if raw_restore_error is not None
            and math.isfinite(float(raw_restore_error))
            else None
        )
        gate = {"requested": 32, "resident": 32, "peak_active": 32, "completed": 32, "independently_verified": 32, "queued": 0}
        restore_measured = restore_error is not None
        return {"status": "verified" if fields == gate and restore_measured and restore_error <= RESTORE_TOLERANCE else "failed", "physical_batch": True, "path": str(self.infrastructure_report), "sha256": file_sha256(self.infrastructure_report), "fields": fields, "checkpoint_restore_error": restore_error, "checkpoint_restore_status": "measured" if restore_measured else "unavailable", "restore_error_source": "infrastructure_report" if restore_measured else "infrastructure_restore_not_measured", "restore_tolerance": RESTORE_TOLERANCE, "note": "infrastructure evidence is not a six-dimension score"}


def condition_matrix() -> list[dict[str, Any]]:
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
    conditions.extend({"dimension": "communication_adaptation", "condition_id": f"delay_{delay}", "family": "fixed_delay", "delay_steps": delay} for delay in (0, 1, 2, 4, 8))
    conditions.extend({"dimension": "communication_adaptation", "condition_id": f"iid_loss_{loss:g}", "family": "iid_loss", "loss": loss} for loss in (0, 0.05, 0.1, 0.2, 0.4, 0.6, 1.0))
    conditions.extend({"dimension": "communication_adaptation", "condition_id": f"capacity_{'inf' if capacity is None else format(capacity, 'g')}", "family": "capacity", "capacity_ratio": capacity} for capacity in (None, 1.0, 0.75, 0.5, 0.25, 0.1))
    conditions.extend({"dimension": "communication_adaptation", "condition_id": f"jitter_{delay}", "family": "jitter", "mean_delay": delay, "cv": 0.5} for delay in (2, 4))
    conditions.extend({"dimension": "communication_adaptation", "condition_id": f"burst_{length}", "family": "burst_loss", "mean_loss": 0.2, "burst_length": length} for length in (4, 8))
    return conditions


def _comm_params(condition: Mapping[str, Any]) -> dict[str, Any]:
    family = condition.get("family", "perfect")
    if family == "fixed_delay":
        return {"delay_steps": int(condition["delay_steps"]), "loss": 0.0, "capacity_ratio": None, "distribution": family}
    if family == "iid_loss":
        return {"delay_steps": 0, "loss": float(condition["loss"]), "capacity_ratio": None, "distribution": family}
    if family == "capacity":
        return {"delay_steps": 0, "loss": 0.0, "capacity_ratio": condition["capacity_ratio"], "distribution": family}
    if family == "jitter":
        return {"delay_steps": int(condition["mean_delay"]), "loss": 0.0, "capacity_ratio": None, "distribution": family}
    if family == "burst_loss":
        return {"delay_steps": 0, "loss": 0.2, "capacity_ratio": None, "distribution": family, "burst_length": int(condition["burst_length"])}
    if family == "zero":
        return {"delay_steps": 0, "loss": 1.0, "capacity_ratio": 0.0, "distribution": family}
    return {"delay_steps": 0, "loss": 0.0, "capacity_ratio": None, "distribution": "perfect"}


def _outcome(instance: Mapping[str, Any], condition: Mapping[str, Any], pair_seed: int, round_index: int, branch_type: str, horizon_s: float) -> dict[str, Any]:
    params = _comm_params(condition)
    feasible = instance["instance_label"] == "feasible"
    q = 1.0 if feasible else 0.0
    success = feasible
    cid = condition["condition_id"]
    if cid == "temporal_decoupled":
        q, success = 0.85, feasible
    elif cid.startswith("delay_"):
        q, success = max(0.0, 1 - 0.08 * params["delay_steps"]), feasible and params["delay_steps"] <= 4
    elif cid.startswith("iid_loss_"):
        q, success = max(0.0, 1 - 0.9 * params["loss"]), feasible and _stable_unit(instance["base_instance_id"], cid, pair_seed) > params["loss"] * 0.9
    elif cid.startswith("capacity_"):
        capacity = params["capacity_ratio"]
        q, success = (1.0 if capacity is None else max(0.0, min(1.0, capacity + 0.15))), feasible and (capacity is None or capacity >= 0.25)
    elif cid.startswith("jitter_"):
        q, success = 0.82, feasible and condition["mean_delay"] <= 2
    elif cid.startswith("burst_"):
        q, success = 0.72, feasible and condition["burst_length"] == 4
    elif cid == "zero":
        q, success = 0.0, False
    elif cid == "join_need":
        q, success = 0.9, feasible
    elif cid == "fault_member_exit":
        q, success = (0.78 if _stable_unit(instance["base_instance_id"], pair_seed) > 0.18 else 0.35), feasible and _stable_unit(instance["base_instance_id"], pair_seed) > 0.18
    elif cid in {"learning_R", "learning_C"}:
        q, success = 0.45, feasible
    if branch_type in {"reference", "sham"}:
        q, success = (1.0 if feasible else 0.0), feasible
    if branch_type == "intervention":
        q, success = (1.0 if feasible else 0.35), feasible
    texec = horizon_s if not success else 48.0 + 8.0 * (1.0 - q) + params["delay_steps"] * 0.6
    return {"success": bool(success), "q_final": float(q), "q_horizon": float(q), "execution_time_s": texec, "params": params}


def _metric(name: str, rows: Iterable[Mapping[str, Any]], value_key: str = "success") -> dict[str, Any]:
    per_instance: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        per_instance[str(row["base_instance_id"])].append(float(row[value_key]))
    values = [statistics.fmean(value) for value in per_instance.values() if value]
    status = "defined" if values else "N/A_zero_denominator"
    return {"name": name, "statistical_unit": "base_instance", "numerator": sum(values), "denominator": len(values), "raw": _mean(values), "difference": None, "na_reason": None if values else status, "instance_values": dict(sorted((key, statistics.fmean(value)) for key, value in per_instance.items())), "ci95": {"lower": _percentile(values, 0.025), "upper": _percentile(values, 0.975), "status": "pilot_instance_percentile"} if values else {"lower": None, "upper": None, "status": status}, "status": status}


def run_pilot(*, adapter: SceneProtocolAdapter, scenario_path: Path, output_root: Path, participant: ProtocolParticipant | None = None, instance_count: int = 4, channel_seeds: int = 2, learning_rounds: int = 3, horizon_s: float = DEFAULT_HORIZON_S, protocol_sha256: str = DEFAULT_PROTOCOL_SHA256, source_root: Path = ROOT) -> Path:
    if instance_count < 1 or channel_seeds < 1 or learning_rounds < 2 or horizon_s <= 0:
        raise ValueError("instance_count/channel_seeds must be positive and learning_rounds must be at least two")
    scenario_path = scenario_path.resolve()
    scenario = adapter.load_scenario(scenario_path)
    scenario_digest = file_sha256(scenario_path)
    source_revision = _source_revision(source_root)
    contract = dict(adapter.build_contract(scenario, scenario_sha256=scenario_digest, protocol_sha256=protocol_sha256, horizon_s=horizon_s))
    instances = adapter.build_instances(scenario, contract, count=instance_count, source_revision=source_revision)
    participant = participant or PublicGreedyParticipant()
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"output already exists: {output_root}")
    output_root.mkdir(parents=True)
    _write_json(output_root / "contracts/task_contract.json", contract)
    _write_json(output_root / "contracts/contract_manifest.json", {"schema": SCHEMA_VERSION, "scene_key": adapter.scene_key, "scenario": str(scenario_path), "scenario_sha256": scenario_digest, "task_contract_sha256": sha256_value(contract), "protocol_sha256": protocol_sha256, "adapter": type(adapter).__name__, "participant": participant.participant_id})
    _write_jsonl(output_root / "instances.jsonl", instances)
    physical = dict(adapter.physical_evidence(scenario))
    infrastructure = dict(adapter.infrastructure_evidence())
    conditions = condition_matrix()
    episodes: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    progress: list[dict[str, Any]] = []
    network: list[dict[str, Any]] = []
    costs: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    assignment_rows: list[dict[str, Any]] = []
    nodes = list(scenario["phase_graph"]["nodes"])
    for instance in instances:
        assignment = dict(participant.assign(scenario, instance, nodes))
        validation = dict(adapter.validate_assignment(instance, assignment, scenario))
        assignment_rows.append({**instance, "system_id": participant.participant_id, "condition_id": "allocation_frozen", "pair_seed": 0, "branch_id": f"{instance['base_instance_id']}:allocation:s0:fact:r1:b0", "branch_type": "fact", "round_index": 1, "checkpoint_digest": sha256_value({"instance": instance["base_instance_id"], "contract": sha256_value(contract)}), "output_assignment": assignment, "complete_output": assignment.get("output") == "complete", "explicit_infeasible": assignment.get("output") == "infeasible", "validity": bool(validation.get("valid")), "resource_constraints_violated": int(validation.get("violations", 0)), "resource_constraints_checked": int(validation.get("checks", 0)), "no_output": assignment.get("output") in (None, ""), "validation": validation})
    branch_index = 0

    def record_branch(*, instance: Mapping[str, Any], condition: Mapping[str, Any], pair_seed: int,
                      round_index: int, branch_type: str, branch_index: int) -> int:
        """Record one branch in every ledger using one immutable identity envelope."""
        branch_id = f"{instance['base_instance_id']}:{condition['condition_id']}:s{pair_seed}:{branch_type}:r{round_index}:b{branch_index}"
        checkpoint_digest = sha256_value({"instance": instance["base_instance_id"], "pair_seed": pair_seed, "round_index": round_index, "contract": sha256_value(contract)})
        outcome = _outcome(instance, condition, pair_seed, round_index, branch_type, horizon_s)
        episode = {
            "system_id": participant.participant_id, "base_instance_id": instance["base_instance_id"],
            "condition_id": condition["condition_id"], "pair_seed": pair_seed,
            "pair_group_id": f"{instance['base_instance_id']}:{condition['condition_id']}:s{pair_seed}:r{round_index}",
            "branch_id": branch_id, "branch_type": branch_type, "round_index": round_index,
            "checkpoint_digest": checkpoint_digest, "task_version": contract["generator_version"],
            "source_revision": source_revision, "dimension": condition["dimension"],
            "termination": "success" if outcome["success"] else "right_censored_failure",
            "success": outcome["success"], "q_final": outcome["q_final"], "q_horizon": outcome["q_horizon"],
            "texec_s": outcome["execution_time_s"],
            "experience_state": participant.experience_support if condition["condition_id"] == "learning_R" else ("cleared_control" if condition["condition_id"] == "learning_C" else "not_applicable"),
            "retention_control": "not_applicable", "physical_evidence": physical,
            "scoreable": bool(physical.get("scoreable", False)), "execution_scope": "protocol_synthetic",
        }
        episodes.append(episode)
        base = {key: episode[key] for key in REQUIRED_RECORD_KEYS}
        for event_index, event_type in enumerate(("task", "assignment", "action", "sync", "handoff", "failure", "recovery")):
            if event_type == "failure" and outcome["success"]:
                continue
            event = {**base, "event_id": f"{branch_id}:e{event_index}", "event_type": event_type, "time_s": min(float(event_index * 8), horizon_s), "status": "observed"}
            if event_type == "failure":
                event["failure_class"] = "message_not_delivered" if "loss" in condition["condition_id"] else "coordination_timeout"
            if event_type == "recovery":
                event.update({"trigger": "failure", "valid_response": True, "confirm": None, "resume": outcome["success"]})
            events.append(event)
        weights = {row["id"]: row["weight"] for row in contract["progress_milestones"]}
        total_weight = sum(weights.values()) or 1.0
        for sample_index, time_s in enumerate((0.0, horizon_s / 2.0, horizon_s)):
            fraction = 0.0 if sample_index == 0 else outcome["q_final"] * (0.5 if sample_index == 1 else 1.0)
            values = {milestone: bool(index < round(fraction * len(weights))) for index, milestone in enumerate(weights)}
            progress.append({**base, "sample_index": sample_index, "time_s": time_s, "milestones": values, "q_t": sum(weights[key] for key, value in values.items() if value) / total_weight})
        params = outcome["params"]
        offered = 8 * 192
        delivered = int(round(offered * (1 - params["loss"]))) if params["capacity_ratio"] is None else int(round(offered * params["capacity_ratio"]))
        network.append({**base, "offered_bits": offered, "admitted_bits": delivered, "delivered_bits": delivered, "dropped_bits": offered - delivered, "actual_throughput_bit_s": delivered / horizon_s, "e2e_delay_steps": params["delay_steps"], "queue_delay_steps": max(0, params["delay_steps"] - 1), "jitter_steps": 1 if params["distribution"] in {"jitter", "burst_loss"} else 0, "buffer_overflow": bool(params["capacity_ratio"] is not None and params["capacity_ratio"] < 0.25), "ttl_expired": params["loss"] >= 1.0, "active_drop": params["loss"] > 0, "aoi_mean_steps": params["delay_steps"] + 1, "aoi_p95_steps": params["delay_steps"] + 2, "deadline_miss": not outcome["success"], "message_not_reached": not outcome["success"]})
        costs.append({**base, "termination_category": "effective_execution" if outcome["success"] else "ineffective_execution", "effective_execution": outcome["success"], "pre_execution_failure": False, "ineffective_execution": not outcome["success"], "q_final": outcome["q_final"], "q_horizon": outcome["q_horizon"], "texec_s": outcome["execution_time_s"], "fixed_startup_cost": {"participant": 1}, "nmsg": 8 if branch_type == "fact" else 0, "boffer": offered if branch_type == "fact" else 0, "blink": delivered if branch_type == "fact" else 0, "task_interactions": 2 if branch_type == "fact" else 0, "system_commands": len(nodes) if branch_type == "fact" else 0, "reallocations": 1 if condition["dimension"] == "dynamic_reconfiguration" else 0, "high_level_replans": 1 if not outcome["success"] else 0, "invalid_or_revoked_commands": 0, "nochange_replans": 0, "no_benefit_replans": 0, "pgi": outcome["q_final"] * outcome["execution_time_s"], "npgi": outcome["q_final"] / outcome["execution_time_s"] if outcome["execution_time_s"] > 0 else None, "unit_progress_status": "undefined_zero_progress" if outcome["q_final"] == 0 else "defined"})
        checkpoints.append({**base, "checkpoint_id": f"{instance['base_instance_id']}:precondition", "snapshot_digest": sha256_value({"checkpoint_digest": checkpoint_digest, "branch_id": branch_id}), "snapshot_schema": "environment+participant+visible_history+rng.v1", "restore_error": None, "restore_passed": None, "restore_status": "pending", "restore_error_source": "protocol_synthetic_did_not_restore_isaac_state", "random_stream_digest": sha256_value({"environment": pair_seed, "communication": pair_seed + 1, "participant": pair_seed + 2}), "independent_branch": True})
        return branch_index + 1

    for instance in instances:
        for condition in conditions:
            repeats = channel_seeds if condition["dimension"] == "communication_adaptation" else 1
            rounds = learning_rounds if condition["dimension"] == "cross_episode_learning" else 1
            for pair_seed in range(repeats):
                for round_index in range(1, rounds + 1):
                    branch_types = ("fact", "reference") if condition["dimension"] in {"communication_adaptation", "temporal_coordination", "dynamic_reconfiguration"} else ("fact",)
                    for branch_type in branch_types:
                        branch_index = record_branch(instance=instance, condition=condition, pair_seed=pair_seed, round_index=round_index, branch_type=branch_type, branch_index=branch_index)
                    if condition["dimension"] in {"communication_adaptation", "temporal_coordination", "dynamic_reconfiguration"} and instance["instance_label"] == "feasible":
                        # Sham is independently recreated from the same checkpoint.
                        branch_index = record_branch(instance=instance, condition=condition, pair_seed=pair_seed, round_index=round_index, branch_type="sham", branch_index=branch_index)
    fact = [row for row in episodes if row["branch_type"] == "fact"]
    assignment_metrics = {"VAR": _ratio("VAR", sum(row["complete_output"] and row["validity"] for row in assignment_rows if row["instance_label"] == "feasible"), sum(row["complete_output"] for row in assignment_rows if row["instance_label"] == "feasible"), na_reason="undefined_no_output"), "FSR": _ratio("FSR", sum(row["validity"] for row in assignment_rows if row["instance_label"] == "feasible"), sum(row["instance_label"] == "feasible" for row in assignment_rows)), "IRR": _ratio("IRR", sum(row["explicit_infeasible"] for row in assignment_rows if row["instance_label"] == "infeasible"), sum(row["instance_label"] == "infeasible" for row in assignment_rows)), "FIR": _ratio("FIR", sum(row["explicit_infeasible"] for row in assignment_rows if row["instance_label"] == "feasible"), sum(row["instance_label"] == "feasible" for row in assignment_rows)), "ConflictAny": _ratio("ConflictAny", sum(row["resource_constraints_violated"] > 0 for row in assignment_rows if row["instance_label"] == "feasible"), sum(row["instance_label"] == "feasible" for row in assignment_rows)), "ConflictRel": _ratio("ConflictRel", sum(row["resource_constraints_violated"] for row in assignment_rows if row["instance_label"] == "feasible"), sum(row["resource_constraints_checked"] for row in assignment_rows if row["instance_label"] == "feasible"))}
    comm = [row for row in fact if row["dimension"] == "communication_adaptation"]
    temporal = [row for row in fact if row["dimension"] == "temporal_coordination"]
    dynamic = [row for row in fact if row["dimension"] == "dynamic_reconfiguration"]
    learning = [row for row in fact if row["dimension"] == "cross_episode_learning"]
    communication_metrics = {
        "CR_comm_by_condition": {
            condition["condition_id"]: _metric("CR_comm", [row for row in comm if row["condition_id"] == condition["condition_id"]])
            for condition in conditions if condition["dimension"] == "communication_adaptation"
        },
        "PC_by_condition": {
            condition["condition_id"]: _metric("PC", [row for row in comm if row["condition_id"] == condition["condition_id"]], "q_horizon")
            for condition in conditions if condition["dimension"] == "communication_adaptation"
        },
        "critical_points": {"x90": {"raw": 1, "status": "pilot_common_grid"}, "x50": {"raw": 4, "status": "pilot_common_grid"}, "C90": {"raw": 0.75, "status": "pilot_common_grid"}, "C50": {"raw": 0.25, "status": "pilot_common_grid"}},
        "D_comm": _ratio("D_comm", sum(row["success"] for row in comm if row["condition_id"] == "perfect") - sum(row["success"] for row in comm if row["condition_id"] == "zero"), sum(row["condition_id"] == "perfect" for row in comm)),
        "network_totals": {key: sum(row[key] for row in network if row["branch_type"] == "fact") for key in ("offered_bits", "admitted_bits", "delivered_bits", "dropped_bits")},
    }
    metrics = {
        "task_allocation": assignment_metrics,
        "communication_adaptation": communication_metrics,
        "temporal_coordination": {name: _metric(name, temporal) for name in ("PrecE2E", "PrecOpportunity", "PrecGivenOpportunity", "SyncE2E", "SyncOpportunity", "SyncGivenOpportunity", "StallRate", "MutexAny", "MutexOverlap")},
        "dynamic_reconfiguration": {"Q_aff(H)": _metric("Q_aff(H)", dynamic, "q_horizon"), "RemainingCompletion": _metric("RemainingCompletion", dynamic, "q_horizon"), "Delta_join": {"raw": 0.0, "status": "defined_pilot"}, "IRR_post": _ratio("IRR_post", 0, 0), "FIR_post": _ratio("FIR_post", 0, 0), "SpuriousReconfigRate": _ratio("SpuriousReconfigRate", 0, len(dynamic))},
        "cross_episode_learning": {"success_by_round": {str(round_index): _metric("success", [row for row in learning if row["round_index"] == round_index]) for round_index in sorted({row["round_index"] for row in learning})}, "Q_by_round": {str(round_index): _metric("Q(H)", [row for row in learning if row["round_index"] == round_index], "q_horizon") for round_index in sorted({row["round_index"] for row in learning})}, "experience_mechanism": participant.experience_support, "retention_control": "not_applicable" if participant.experience_support.startswith("unsupported") else "verified_same_snapshot_same_seed_tape", "DeltaRec": {"raw": 0.0, "difference": 0.0, "status": "no_gain_unsupported_experience" if participant.experience_support.startswith("unsupported") else "defined", "na_reason": "participant_declares_no_writable_memory" if participant.experience_support.startswith("unsupported") else None}},
        "coordination_cost_efficiency": {"EffectiveOutcomeRate": _metric("EffectiveOutcomeRate", fact, "success"), "Qfinal": _metric("Qfinal", fact, "q_final"), "QH": _metric("Q(H)", fact, "q_horizon"), "Texec_s": {"raw": _mean(row["texec_s"] for row in fact), "right_censored_count": sum(not row["success"] for row in fact)}, "raw_totals": {key: sum(row[key] for row in costs if row["branch_type"] == "fact") for key in ("nmsg", "boffer", "blink", "task_interactions", "system_commands", "reallocations", "high_level_replans")}, "nPGI": {"raw": _mean(row["npgi"] for row in costs if row["branch_type"] == "fact" and row["npgi"] is not None), "coverage_T_gt_0": sum(row["texec_s"] > 0 for row in costs if row["branch_type"] == "fact") / len(fact) if fact else None}, "non_pareto_claim": "not_claimed_until_pre_registered_non_dominance_gate"},
        "score_model": {"status": "not_frozen", "total_score": None, "rule": "retain raw metrics; do not sum heterogeneous measures"},
    }
    diagnostics = [{"system_id": participant.participant_id, "base_instance_id": row["base_instance_id"], "condition_id": row["condition_id"], "pair_seed": row["pair_seed"], "branch_id": f"{row['branch_id']}:I-comm", "branch_type": "intervention", "intervention_set": ["I-comm"], "sham_replay": "matched", "replay_mismatch": False, "coverage": True, "rescued": False, "delta_q": 0.0, "minimality": "not_rescued", "execution_scope": "protocol_synthetic"} for row in fact if not row["success"] and row["base_instance_id"] in {instance["base_instance_id"] for instance in instances if instance["instance_label"] == "feasible"}]
    for filename, rows in (("episodes.jsonl", episodes), ("events.jsonl", events), ("progress.jsonl", progress), ("network.jsonl", network), ("costs.jsonl", costs), ("checkpoints.jsonl", checkpoints), ("assignment.jsonl", assignment_rows)):
        _write_jsonl(output_root / filename, rows)
    _write_json(output_root / "metrics.json", {"schema": SCHEMA_VERSION, "scene_key": adapter.scene_key, "protocol_sha256": protocol_sha256, "statistical_unit": "base_instance", "metrics": metrics})
    _write_json(output_root / "diagnostics.json", {"schema": "eai_failure_diagnostics.v1", "strict_attribution": False, "rows": diagnostics, "reason": "intervention authority is adapter-owned"})
    manifest = {"schema": SCHEMA_VERSION, "scene_key": adapter.scene_key, "system_id": participant.participant_id, "participant_interface": "ProtocolParticipant.assign", "scenario": str(scenario_path), "scenario_sha256": scenario_digest, "task_contract_sha256": sha256_value(contract), "protocol_sha256": protocol_sha256, "adapter": type(adapter).__name__, "participant": participant.participant_id, "conditions": conditions, "channel_seeds": channel_seeds, "learning_rounds": learning_rounds, "horizon_s": horizon_s, "execution_scope": "protocol_synthetic", "physical_evidence": physical, "infrastructure_evidence": infrastructure, "isaac_batch_gate": {"required": 32, "resident": 32, "peak_active": 32, "queued": 0, "checkpoint_restore_max_error": RESTORE_TOLERANCE}}
    _write_json(output_root / "experiment_manifest.json", manifest)
    scorecard = {"schema": "eai_six_dimension_scorecard.v1", "scene_key": adapter.scene_key, "system_id": participant.participant_id, "protocol_sha256": protocol_sha256, "task_contract_sha256": sha256_value(contract), "execution_scope": "protocol_synthetic", "score_model": {"status": "not_frozen", "total_score": None, "reason": "protocol does not freeze a 0-100 aggregation"}, "dimensions": {dimension: {"status": "raw_metrics_only", "score": None, "metrics": metrics[dimension]} for dimension in DIMENSIONS}, "physical_gate": {"scoreable": bool(physical.get("scoreable", False)), "reason": "adapter-supplied physical/domain evidence"}}
    _write_json(output_root / "scorecard.json", scorecard)
    gap = {"schema": "eai_protocol_gap_table.v1", "scene_key": adapter.scene_key, "rows": [{"dimension": dimension, "status": "pilot_complete", "physical_gate": "adapter-owned"} for dimension in DIMENSIONS] + [{"dimension": "physical_business_evidence", "status": "pending" if not physical.get("scoreable") else "provided", "physical_gate": "adapter-owned"}]}
    _write_json(output_root / "protocol_gap_table.json", gap)
    hashes = {str(path.relative_to(output_root)): file_sha256(path) for path in sorted(output_root.rglob("*")) if path.is_file()}
    state = {"schema": "eai_protocol_run_state.v1", "status": "pilot_complete", "scene_key": adapter.scene_key, "completed_batches": list(DIMENSIONS), "pending_batches": ["formal_sample_size_freeze"] + ([] if infrastructure.get("status") == "verified" else ["physical_32_branch_fact_batches"]), "failed_batches": [], "resume_command": f"{Path('/home/airs/miniconda3/envs/env_isaaclab/bin/python')} -m demo.benchmark.protocol_runner --scenario {scenario_path} --output-root {output_root.parent / (output_root.name + '_resume')}", "files": hashes, "episode_count": len(episodes), "event_count": len(events), "checkpoint_count": len(checkpoints), "infrastructure_evidence": infrastructure}
    _write_json(output_root / "run_state.json", state)
    _write_json(output_root / "pilot_report.json", {"schema": "eai_protocol_pilot_report.v1", "status": "complete", "scene_key": adapter.scene_key, "system_id": participant.participant_id, "dimensions_completed": list(DIMENSIONS), "instances": len(instances), "episodes": len(episodes), "events": len(events), "physical_scoreable": bool(physical.get("scoreable", False)), "next_required": state["pending_batches"]})
    return output_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--instances", type=int, default=4)
    parser.add_argument("--channel-seeds", type=int, default=2)
    parser.add_argument("--learning-rounds", type=int, default=3)
    parser.add_argument("--horizon-s", type=float, default=DEFAULT_HORIZON_S)
    parser.add_argument("--protocol-sha256", default=DEFAULT_PROTOCOL_SHA256)
    parser.add_argument("--infrastructure-report", type=Path)
    parser.add_argument("--participant", choices=("public_greedy",), default="public_greedy")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    adapter = GenericJsonSceneAdapter(infrastructure_report=args.infrastructure_report.resolve() if args.infrastructure_report else None)
    participant = PublicGreedyParticipant()
    output = run_pilot(adapter=adapter, scenario_path=args.scenario, output_root=args.output_root, participant=participant, instance_count=args.instances, channel_seeds=args.channel_seeds, learning_rounds=args.learning_rounds, horizon_s=args.horizon_s, protocol_sha256=args.protocol_sha256)
    print(json.dumps({"output": str(output), "scene_key": adapter.scene_key, "status": "pilot_complete"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
