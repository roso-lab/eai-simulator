"""Room 06 adapter for the scene-agnostic Isaac-free diagnostic runtime.

The latest semantic-map revision is the only accepted map input.  The map has
two disconnected ground-level components (the published cross-floor edge is
not yet a physical witness), so the logical contract models the cross-floor
relay as an aerial transition obligation and keeps the limitation explicit in
the contract.  Runtime task ordering, communication, mutexes, checkpoints and
replay are still evaluated by :class:`DiagnosticRuntime`.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from benchmark.hetero_diagnostic_runtime_v1 import (
    CONTROL_DT_S, DiagnosticRuntime, EpisodeSpec, GreedyGoalAlgorithm,
    ResourceSpec, RobotSpec, SemanticEdge, SemanticGraph, SemanticNode, TaskSpec,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "usd/scene/Living and public space/room_06/semantic_map/current"
DEFAULT_MANIFEST = ROOT / "benchmark/tasks/room_06/room_06_task_manifest.v1.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _edge_length(edge: Mapping[str, Any], positions: Mapping[str, tuple[float, float, float]]) -> float:
    points = [tuple(float(v) for v in p) for p in edge.get("polyline_xyz", ())]
    if len(points) < 2:
        points = [positions[edge["from"]], positions[edge["to"]]]
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


@dataclass(frozen=True)
class Room06ScenarioAdapter:
    map_root: Path = DEFAULT_MAP
    manifest_path: Path = DEFAULT_MANIFEST

    @property
    def scenario_id(self) -> str:
        return "room_06"

    def manifest(self) -> dict[str, Any]:
        return _load(self.manifest_path)

    def semantic_graph(self) -> SemanticGraph:
        points = _load(self.map_root / "semantic-points.json")["points"]
        nodes: list[SemanticNode] = []
        edges: list[SemanticEdge] = []
        for domain, filename in (("ground", "topology.json"), ("aerial", "topology-aerial.json")):
            topology = _load(self.map_root / filename)
            positions = {row["id"]: tuple(float(v) for v in row["position_xyz"]) for row in topology["nodes"]}
            for node_id, position in sorted(positions.items()):
                level = next((row.get("level_id") for row in topology["nodes"] if row["id"] == node_id), None)
                semantics = (str(level),) if level else ()
                nodes.append(SemanticNode(f"{domain}::{node_id}", domain, position, semantics))
            for edge in topology["edges"]:
                edges.append(SemanticEdge(f"{domain}::{edge['from']}", f"{domain}::{edge['to']}", _edge_length(edge, positions), bidirectional=not bool(edge.get("directed", False))))
        graph = SemanticGraph(nodes, edges)
        for point in points:
            label = str(point["label"])
            for binding in point.get("navigation_bindings", ()):
                graph.bind(label, f"{binding['domain']}::{binding['node_id']}")
        # The current product has no named transition node or ground edge
        # joining 1F and 2F.  This explicit logical proxy is kept out of the
        # public scene-map file and is marked non-physical in the contract.
        graph.bind("room06_transition_handoff", "aerial::node:0020")
        return graph

    def _tier(self, tier: int) -> Mapping[str, Any]:
        for row in self.manifest()["difficulty_tiers"]:
            if int(row["difficulty_tier"]) == int(tier):
                return row
        raise ValueError(f"room_06 tier must be one of 1..4, got {tier}")

    def _condition(self, tier: int, condition_id: str | None) -> Mapping[str, Any]:
        selected = condition_id or self._tier(tier)["default_condition"]
        for row in self.manifest()["conditions"]:
            if row["condition_id"] == selected:
                return row
        raise ValueError(f"unknown room_06 condition {selected!r}")

    def build_episode(self, *, tier: int = 4, condition_id: str | None = None,
                      communication: str | None = None, seed: int = 1) -> EpisodeSpec:
        manifest = self.manifest()
        tier_row = self._tier(tier)
        condition = self._condition(tier, condition_id)
        selected_condition = communication or str(condition["communication"])
        graph = self.semantic_graph()
        robots = tuple(
            RobotSpec(row["robot_id"], row["domain"], f"{row['domain']}::{row['initial_point']}",
                      speed_mps=float(row["speed_mps"]), capabilities=tuple(row["capabilities"]))
            for row in manifest["robot_roster"]
        )
        tasks: list[TaskSpec] = []
        book_count = int(tier_row["books"])
        destinations = tuple(manifest["semantic_points"]["book_destinations"])
        all_places: list[str] = []
        for index in range(1, book_count + 1):
            pick = f"book_{index}_pick"
            handoff = f"book_{index}_handoff"
            place = f"book_{index}_place"
            tasks.append(TaskSpec(pick, "inspection", (manifest["semantic_points"]["book_source"],),
                                  required_domain="ground", required_capabilities=("payload_transport", "source_access"),
                                  report_required=True, public=True, public_group="relay_pick", public_rank=index))
            tasks.append(TaskSpec(handoff, "handoff", ("room06_transition_handoff",), prerequisites=(pick,),
                                  required_domain="aerial", required_capabilities=("aerial_relay",), resource_id="transition_capacity",
                                  service_ticks=30, report_required=True, public=True, announce_after=(pick,),
                                  public_group="relay_handoff", public_rank=index))
            destination = destinations[index - 1]
            tasks.append(TaskSpec(place, "inspection", (destination,), prerequisites=(handoff,), required_domain="ground",
                                  required_capabilities=("payload_transport", "upper_floor_access"), report_required=True,
                                  public=True, announce_after=(handoff,), public_group="relay_place", public_rank=index))
            all_places.append(place)
        if bool(tier_row["joint_carry"]):
            joint_group = str(tier_row.get("joint_group", "heavy_carry"))
            tasks.extend([
                TaskSpec("heavy_carry_left", "response", (manifest["semantic_points"]["heavy_destination"],),
                         prerequisites=tuple(all_places), required_domain="ground", required_capabilities=("joint_carry", "upper_floor_access"),
                         resource_id="joint_carry_capacity", service_ticks=2000, distinct_group="heavy_carry",
                         cooperative_group=joint_group, report_required=True, public=True),
                TaskSpec("heavy_carry_right", "response", (manifest["semantic_points"]["heavy_destination"],),
                         prerequisites=tuple(all_places), required_domain="ground", required_capabilities=("joint_carry", "upper_floor_access"),
                         resource_id="joint_carry_capacity", service_ticks=2000, distinct_group="heavy_carry",
                         cooperative_group=joint_group, report_required=True, public=True),
            ])
        deadline_table = manifest["timing_policy"]["hard_deadline_ticks"]
        deadline = int(deadline_table.get(f"t{tier}:{selected_condition}", deadline_table.get(f"t{tier}:C0", 17000)))
        resources = (ResourceSpec("transition_capacity", "aerial::node:0020", capacity=1, service_ticks=30),
                     ResourceSpec("joint_carry_capacity", "ground::node:0093", capacity=1, service_ticks=2000))
        return EpisodeSpec(graph, robots, tuple(tasks), resources=resources, horizon_ticks=max(20000, deadline + 1000),
                           hard_deadline_tick=deadline, dt_s=CONTROL_DT_S, seed=seed,
                           communication_condition=selected_condition,
                           availability_loss_task=("book_2_handoff" if tier >= 4 and condition.get("availability_loss") else None))

    def contract(self, *, tier: int = 4, condition_id: str | None = None) -> dict[str, Any]:
        manifest = self.manifest(); condition = self._condition(tier, condition_id)
        tier_row = self._tier(tier)
        deadline = int(manifest["timing_policy"]["hard_deadline_ticks"].get(f"t{tier}:{condition['condition_id']}", manifest["timing_policy"]["hard_deadline_ticks"].get(f"t{tier}:C0", 17000)))
        return {"scenario_id": self.scenario_id, "scene_key": manifest["scene_key"], "task_family": manifest["task_family"],
                "tier": tier, "task_id": tier_row["task_id"], "condition_id": condition["condition_id"],
                "relation_signature": tier_row["relation_signature"], "map_revision": manifest["semantic_map"]["revision"],
                "semantic_binding_digest": manifest["semantic_map"]["semantic_binding_digest"],
                "checkpoint_policy": manifest["checkpoint_policy"], "communication": condition["communication"],
                "hard_deadline_tick": deadline, "hard_deadline_s": deadline * CONTROL_DT_S,
                "cross_floor_route_status": "logical_proxy_pending_physical_witness",
                "digest": _digest({"manifest": manifest, "tier": tier, "condition": condition["condition_id"]})}

    def run_generic(self, *, tier: int = 4, condition_id: str | None = None,
                    communication: str | None = None, algorithm: Any | None = None,
                    seed: int = 1) -> dict[str, Any]:
        runtime = DiagnosticRuntime(self.build_episode(tier=tier, condition_id=condition_id, communication=communication, seed=seed),
                                    algorithm=algorithm or GreedyGoalAlgorithm())
        checkpoint = runtime.checkpoint(metadata={"schema": runtime.schema_version, "scenario": self.scenario_id, "tier": str(tier)})
        result = runtime.run()
        replay = runtime.replay(checkpoint)
        result["checkpoint"] = checkpoint
        result["branch_digest"] = runtime.branch(checkpoint).replay_digest(checkpoint)
        result["replay_equivalent"] = bool(replay.get("replay_equivalent"))
        result["checkpoints"] = self._milestone_checkpoints(result)
        result["scenario"] = self.contract(tier=tier, condition_id=condition_id)
        result["diagnostic_metrics"] = self.diagnostic_metrics(result)
        return result

    @staticmethod
    def _milestone_checkpoints(result: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Project evaluator events into stable phase checkpoints.

        These are monitoring milestones, not additional navigation nodes.  A
        checkpoint is emitted only after the corresponding task/event ledger
        entry exists, keeping phase completion distinct from mere arrival.
        """
        events = list(result.get("events", ()))
        completed = {str(row.get("task_id")): int(row.get("tick", 0)) for row in events if row.get("kind") == "task_completed"}
        assigned = [int(row.get("tick", 0)) for row in events if row.get("kind") == "task_assigned"]
        def first(prefix: str) -> int | None:
            ticks = [tick for task, tick in completed.items() if task.startswith(prefix)]
            return min(ticks) if ticks else None
        def last(prefix: str) -> int | None:
            ticks = [tick for task, tick in completed.items() if task.startswith(prefix)]
            return max(ticks) if ticks else None
        rows: list[dict[str, Any]] = [{"label": "cp_00_spawn_valid", "tick": 0}]
        if assigned:
            rows.append({"label": "cp_01_books_assigned", "tick": min(assigned)})
        if first("book_") is not None:
            rows.append({"label": "cp_02_relay_started", "tick": first("book_1_pick")})
        if first("book_1_handoff") is not None:
            rows.append({"label": "cp_03_transition_handoff", "tick": first("book_1_handoff")})
        if last("book_") is not None and any(task.endswith("_place") for task in completed):
            rows.append({"label": "cp_04_books_complete", "tick": last("book_")})
        if first("heavy_carry_") is not None:
            rows.append({"label": "cp_05_joint_carry_started", "tick": first("heavy_carry_")})
        if last("heavy_carry_") is not None:
            rows.append({"label": "cp_06_joint_carry_complete", "tick": last("heavy_carry_")})
        if result.get("success"):
            rows.append({"label": "cp_07_closeout", "tick": int(result.get("completion_tick", result.get("tick", 0)))})
        return rows

    @staticmethod
    def diagnostic_metrics(result: Mapping[str, Any]) -> dict[str, Any]:
        events = list(result.get("events", ()))
        def first(kind: str) -> int | None:
            ticks = [int(row["tick"]) for row in events if row.get("kind") == kind]
            return min(ticks) if ticks else None
        return {"success": bool(result.get("success")), "makespan_s": float(result.get("makespan_s", 0.0)),
                "resource_contention_requests": int(result.get("resource_contention_requests", 0)),
                "resource_wait_s": float(result.get("resource_wait_ticks", 0)) * CONTROL_DT_S,
                "first_relay_s": (first("task_completed") or 0) * CONTROL_DT_S,
                "availability_loss": bool(result.get("lost_robots")), "communication_loss_bits": int(result.get("communication_loss_bits", 0)),
                "replay_equivalent": bool(result.get("replay_equivalent")), "paper_score_eligible": False,
                "D1": {"status": "diagnostic_only", "feasibility": "logical_proxy_pending_physical_witness"},
                "D2": {"status": "diagnostic_only", "communication": result.get("communication_delay_ticks", 0)},
                "D3": {"status": "diagnostic_only", "resource_contention": int(result.get("resource_contention_requests", 0))},
                "D4": {"status": "diagnostic_only", "availability_loss": bool(result.get("lost_robots"))},
                "D5": {"status": "na", "reason": "no certified cross-scenario pair"},
                "D6": {"status": "pending", "reason": "independent physical calibration required"}}


__all__ = ["Room06ScenarioAdapter"]
