"""city_01 adapter for the scene-agnostic diagnostic benchmark.

The adapter owns only scene data and the frozen city task contract.  Scheduling,
communication, checkpoint and branch semantics belong to the generic runtime;
the legacy fast runner remains available as a compatibility execution path.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from benchmark.city_01_logic_sim import City01Map, run_city01
from benchmark.hetero_diagnostic_runtime_v1 import (
    CONTROL_DT_S, DiagnosticRuntime, EpisodeSpec, GreedyGoalAlgorithm, ResourceSpec,
    RobotSpec, SemanticEdge, SemanticGraph, SemanticNode, TaskSpec,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "usd/scene/Service and operations/city_01/semantic_map/current"
DEFAULT_MANIFEST = ROOT / "benchmark/tasks/city_01/city_01_task_manifest.v1.json"


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class City01ScenarioAdapter:
    """Load and validate city_01, then expose a stable scene adapter boundary."""

    map_root: Path = DEFAULT_MAP
    manifest_path: Path = DEFAULT_MANIFEST

    @property
    def scenario_id(self) -> str:
        return "city_01"

    def manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def semantic_graph(self) -> City01Map:
        return City01Map(self.map_root)

    def build_episode(self, *, tier: int = 4, condition_id: str | None = None,
                      communication: str | None = None) -> EpisodeSpec:
        """Compile city_01 semantic topology into the generic logical runtime.

        Area aliases bind their ground and aerial anchors together, so a
        coverage obligation is satisfied by either valid modality trajectory.
        Battery/recover policy remains on the compatibility track until a
        scene-specific policy adapter is supplied; the generic runtime still
        exposes resources and battery state for other scenes.
        """
        manifest = self.manifest()
        city = self.semantic_graph()
        nodes = [SemanticNode(node_id, city.domains[node_id], city.node_positions[node_id])
                 for node_id in sorted(city.node_positions)]
        graph = SemanticGraph(nodes)
        for source, neighbours in city.graph.items():
            for target, distance in neighbours:
                if source < target:
                    graph.add_edge(SemanticEdge(source, target, distance))
        for label, bindings in city.bindings.items():
            for node_id in bindings:
                graph.bind(label, node_id)
        for area in manifest["areas"]:
            area_id = area["area_id"]
            graph.bind(area_id, city.node(area["ground_anchor"]["label"], "ground"))
            for anchor in area.get("aerial_anchors", ()):
                graph.bind(area_id, city.node(anchor["label"], "aerial"))
        robots = tuple(RobotSpec(item["robot_id"], item["domain"], city.node(item["initial_point"], item["domain"]),
                                 speed_mps=1.0 if item["domain"] == "ground" else 4.0,
                                 capabilities=(item["role"],)) for item in manifest["robot_roster"])
        tasks = [TaskSpec(f"coverage_{area['area_id']}", "coverage", (area["area_id"],)) for area in manifest["areas"]]
        observation_ids = []
        if tier >= 2:
            for row in manifest["incident"]["aerial_observations"]:
                task_id = row["observation_id"]
                observation_ids.append(task_id)
                tasks.append(TaskSpec(task_id, "inspection", (row["semantic_label"],), required_domain="aerial"))
        if tier >= 3:
            required = 3 if tier == 3 else 4
            prereq = tuple(observation_ids)
            for index in range(required):
                tasks.append(TaskSpec(f"response_{index + 1}", "response", ("accident",), prerequisites=prereq, required_domain="ground"))
        resources = tuple(ResourceSpec(item["resource_id"], f"ground::{item['node_id']}", capacity=int(item.get("capacity", 1)),
                                       service_ticks=max(1, round(float(item.get("occupancy_wait_s", 8.0)) / CONTROL_DT_S)), recharges=True)
                          for item in manifest.get("recover_resources", ()))
        horizon = 200000
        return EpisodeSpec(graph, robots, tuple(tasks), resources=resources, horizon_ticks=horizon,
                           dt_s=CONTROL_DT_S, communication_condition=communication or "C0")

    def run_generic(self, *, tier: int = 4, condition_id: str | None = None,
                    communication: str | None = None, algorithm: Any | None = None) -> dict[str, Any]:
        runtime = DiagnosticRuntime(self.build_episode(tier=tier, condition_id=condition_id,
                                                       communication=communication), algorithm=algorithm or GreedyGoalAlgorithm())
        result = runtime.run()
        result["scenario"] = self.contract(tier=tier, condition_id=condition_id)
        result["diagnostic_metrics"] = self.diagnostic_metrics(result)
        return result

    def contract(self, *, tier: int = 4, condition_id: str | None = None) -> dict[str, Any]:
        manifest = self.manifest()
        tiers = {item["difficulty_tier"]: item for item in manifest["difficulty_tiers"]}
        if tier not in tiers:
            raise ValueError(f"city_01 tier must be one of {sorted(tiers)}")
        condition_id = condition_id or tiers[tier]["default_condition"]
        conditions = {item["condition_id"]: item for item in manifest["conditions"]}
        if condition_id not in conditions:
            raise ValueError(f"unknown city_01 condition {condition_id!r}")
        return {
            "scenario_id": self.scenario_id,
            "scene_key": manifest["scene_key"],
            "task_family": manifest["task_family"],
            "tier": tier,
            "task_id": tiers[tier]["task_id"],
            "condition_id": condition_id,
            "relation_signature": tiers[tier]["relation_signature"],
            "map_revision": manifest["semantic_map"]["revision"],
            "semantic_binding_digest": manifest["semantic_map"]["semantic_binding_digest"],
            "checkpoint_policy": manifest["checkpoint_policy"],
            "communication": conditions[condition_id].get("communication", "C0"),
            "digest": _digest({"manifest": manifest, "tier": tier, "condition": condition_id}),
        }

    def run_compatibility(self, *, tier: int = 4, condition_id: str | None = None,
                          battery_budgets_override: Mapping[str, float] | None = None) -> dict[str, Any]:
        """Run the existing city logic track and add generic diagnostic fields."""
        result = run_city01(tier=tier, condition_id=condition_id, map_root=self.map_root,
                            manifest_path=self.manifest_path,
                            battery_budgets_override=battery_budgets_override)
        result["scenario"] = self.contract(tier=tier, condition_id=condition_id)
        result["diagnostic_metrics"] = self.diagnostic_metrics(result)
        return result

    @staticmethod
    def diagnostic_metrics(result: Mapping[str, Any]) -> dict[str, Any]:
        metrics = dict(result.get("metrics", {}))
        events = list(result.get("events", ()))
        by_kind = {kind: [row for row in events if row.get("kind") == kind] for kind in {
            "cp_00_spawn_valid", "cp_02_pre_incident", "cp_03_observation_complete",
            "cp_05_ground_rendezvous", "cp_06_closeout", "recover_released",
        }}
        def first_time(kind: str) -> float | None:
            rows = by_kind[kind]
            return min((float(row["sim_time_s"]) for row in rows), default=None)
        return {
            "success": bool(metrics.get("success", False)),
            "makespan_s": float(result.get("sim_time_s", 0.0)),
            "coverage_fraction": float(metrics.get("coverage_fraction", 0.0)),
            "first_observation_s": first_time("cp_03_observation_complete"),
            "ground_response_s": first_time("cp_05_ground_rendezvous"),
            "resource_services": int(metrics.get("recover_services", 0)),
            "resource_wait_s": sum(float(row.get("metadata", {}).get("wait_s", 0.0)) for row in by_kind["recover_released"]),
            "communication": {"condition": result.get("condition_id"), "available": False, "reason": "compatibility_runner_does_not_meter_transport"},
            "replay_equivalent": False,
            "paper_score_eligible": False,
            "D1": {"status": "diagnostic_only", "initial_plan_validity": bool(result.get("goal_mode"))},
            "D2": {"status": "pending", "reason": "requires paired C0-C3 runtime branches"},
            "D3": {"status": "diagnostic_only", "p2_observation": first_time("cp_03_observation_complete") is not None},
            "D4": {"status": "pending", "reason": "requires availability-loss branch"},
            "D5": {"status": "na", "reason": "no certified cross-scenario pair"},
            "D6": {"status": "pending", "reason": "transport trace and success gate required"},
        }

    def validate_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        metrics = self.diagnostic_metrics(result)
        errors = []
        if result.get("scenario", {}).get("scenario_id") not in (None, self.scenario_id):
            errors.append("scenario_id_mismatch")
        if not 0.0 <= metrics["coverage_fraction"] <= 1.0:
            errors.append("coverage_fraction_out_of_range")
        return {"valid": not errors, "errors": errors, "qualification": dict(result.get("qualification", {}))}


__all__ = ["City01ScenarioAdapter"]
