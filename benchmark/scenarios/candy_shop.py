"""Isaac-free candy_shop benchmark adapter.

The map uses one continuous support layer.  ``floor2-*`` labels are task
semantic identifiers; elevated access is represented by the graph geometry,
stairs/ramp edges and capability predicates in the task contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from benchmark.hetero_diagnostic_runtime_v1 import (
    CONTROL_DT_S, DiagnosticRuntime, EpisodeSpec, RobotSpec, SemanticEdge,
    SemanticGraph, SemanticNode, ResourceSpec, TaskSpec,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "usd/scene/Production and logistics/candy_shop/semantic_map/current"
DEFAULT_MANIFEST = ROOT / "benchmark/tasks/candy_shop/candy_shop_task_manifest.v1.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _edge_length(polyline: list[list[float]] | None,
                 source: tuple[float, float, float],
                 target: tuple[float, float, float]) -> float:
    points = [tuple(float(v) for v in row) for row in (polyline or [source, target])]
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


@dataclass(frozen=True)
class CandyShopScenarioAdapter:
    map_root: Path = DEFAULT_MAP
    manifest_path: Path = DEFAULT_MANIFEST

    @property
    def scenario_id(self) -> str:
        return "candy_shop"

    def manifest(self) -> dict[str, Any]:
        return _load(self.manifest_path)

    def semantic_graph(self) -> SemanticGraph:
        points = _load(self.map_root / "semantic-points.json")
        nodes: list[SemanticNode] = []
        edges: list[SemanticEdge] = []
        for domain, filename in (("ground", "topology.json"), ("aerial", "topology-aerial.json")):
            topology = _load(self.map_root / filename)
            positions = {row["id"]: tuple(float(v) for v in row["position_xyz"]) for row in topology.get("nodes", ())}
            for node_id, position in sorted(positions.items()):
                nodes.append(SemanticNode(f"{domain}::{node_id}", domain, position))
            for edge in topology.get("edges", ()):
                source, target = edge["from"], edge["to"]
                edges.append(SemanticEdge(f"{domain}::{source}", f"{domain}::{target}",
                                          _edge_length(edge.get("polyline_xyz"), positions[source], positions[target])))
        graph = SemanticGraph(nodes, edges)
        for point in points.get("points", ()):
            for binding in point.get("navigation_bindings", ()):
                graph.bind(point["label"], f"{binding['domain']}::{binding['node_id']}")
        # Public task targets are opaque IDs.  The evaluator retains the
        # floor-qualified map labels, while the tested system receives only
        # target identities and positions.
        aliases = {
            "shelf_1": "floor2-shelf-north", "shelf_2": "floor2-shelf-east",
            "shelf_3": "floor2-shelf-west", "counter_1": "floor1-counter-north-external",
            "counter_2": "floor1-counter-east1-external", "counter_3": "floor1-counter-east2-external",
            "gate_external": "floor1-gate-external", "handoff_inner": "floor1-handoff_point-inner",
            "handoff_external": "floor1-handoff_point-external",
        }
        for alias, label in aliases.items():
            for node_id in graph.resolve(label):
                graph.bind(alias, node_id)
        return graph

    def _tier(self, tier: int) -> Mapping[str, Any]:
        rows = {row["difficulty_tier"]: row for row in self.manifest()["difficulty_tiers"]}
        if tier not in rows:
            raise ValueError("candy_shop difficulty tier must be 1..4")
        return rows[tier]

    def _condition(self, condition_id: str | None, tier: int) -> Mapping[str, Any]:
        selected = condition_id or self._tier(tier)["default_condition"]
        rows = {row["condition_id"]: row for row in self.manifest()["conditions"]}
        if selected not in rows:
            raise ValueError(f"unknown candy_shop condition {selected!r}")
        return rows[selected]

    @staticmethod
    def _task(task_id: str, kind: str, target: str, *, prerequisites=(), domain=None,
              capabilities=(), resource=None, service_ticks=0, continuation=None,
              distinct=None, object_id=None, action=None, public=True,
              announce_after=(), report=True, hold=False, release_task=None) -> TaskSpec:
        return TaskSpec(
            task_id, kind, (target,), prerequisites=tuple(prerequisites),
            required_domain=domain, required_capabilities=tuple(capabilities),
            resource_id=resource, service_ticks=service_ticks,
            continuation_group=continuation, distinct_group=distinct,
            object_id=object_id, object_action=action, public=public,
            announce_after=tuple(announce_after), report_required=report,
            hold_on_completion=hold, hold_release_task=release_task,
        )

    def _tasks(self, tier: int) -> tuple[tuple[TaskSpec, ...], tuple[Mapping[str, Any], ...]]:
        gate = self._task("gate_open", "gate", "gate_external", domain="ground",
                          capabilities=("gate_open",), resource="gate_mutex", service_ticks=20)
        tasks: list[TaskSpec] = [gate]
        objects: list[Mapping[str, Any]] = []

        if tier >= 3:
            tasks.extend([
                self._task("inventory_scan_1", "inspection", "shelf_1", domain="aerial",
                            capabilities=("aerial_observer",), prerequisites=("gate_open",)),
                self._task("inventory_scan_2", "inspection", "shelf_2", domain="aerial",
                            capabilities=("aerial_observer",), prerequisites=("inventory_scan_1",)),
            ])

        if tier >= 3:
            tasks.extend([
                self._task("access_1", "resource_access", "handoff_inner", domain="ground",
                            capabilities=("navigation",), resource="handoff_window", service_ticks=50,
                            prerequisites=("inventory_scan_2",), distinct="access"),
                self._task("access_2", "resource_access", "handoff_inner", domain="ground",
                            capabilities=("navigation",), resource="handoff_window", service_ticks=50,
                            prerequisites=("inventory_scan_2",), distinct="access"),
            ])
            if tier >= 4:
                tasks.append(self._task("access_3", "resource_access", "handoff_inner", domain="ground",
                                        capabilities=("navigation",), resource="handoff_window", service_ticks=50,
                                        prerequisites=("inventory_scan_2",)))

        count = {1: 1, 2: 2, 3: 2, 4: 3}[tier]
        shelves = ("shelf_1", "shelf_2", "shelf_3")
        counters = ("counter_1", "counter_2", "counter_3")
        for index in range(1, count + 1):
            object_id = f"candy_order_{index}"
            source, destination = shelves[index - 1], counters[index - 1]
            objects.append({"object_id": object_id, "source": source, "destination": destination})
            prefix = f"order_{index}_"
            prior = ("gate_open",) if index == 1 else (f"order_{index - 1}_place",)
            if tier >= 3:
                prior = ("access_1",) if index == 1 else (f"order_{index - 1}_place",)
            if tier == 1:
                tasks.append(self._task(prefix + "pick", "manipulation", source, prerequisites=prior,
                                        domain="ground", capabilities=("manipulator",), continuation=f"order_{index}",
                                        object_id=object_id, action="pick"))
                tasks.append(self._task(prefix + "place", "manipulation", destination, prerequisites=(prefix + "pick",),
                                        domain="ground", capabilities=("manipulator",), continuation=f"order_{index}",
                                        object_id=object_id, action="place"))
            elif tier == 2:
                tasks.extend([
                    self._task(prefix + "pick", "manipulation", source, prerequisites=prior, domain="ground",
                               capabilities=("loader",), continuation=f"loader_{index}", object_id=object_id, action="pick"),
                    self._task(prefix + "release", "handoff", "handoff_external", prerequisites=(prefix + "pick",),
                               domain="ground", capabilities=("loader",), continuation=f"loader_{index}", object_id=object_id, action="handoff_release"),
                    self._task(prefix + "receive", "handoff", "handoff_external", prerequisites=(prefix + "release",),
                               domain="ground", capabilities=("handoff_receive",), continuation=f"receiver_{index}",
                               distinct=f"receiver_{index}", resource="handoff_window", service_ticks=50,
                               object_id=object_id, action="handoff_receive"),
                    self._task(prefix + "place", "manipulation", destination, prerequisites=(prefix + "receive",), domain="ground",
                               capabilities=("manipulator",), continuation=f"receiver_{index}", object_id=object_id, action="place"),
                ])
            else:
                access_id = f"access_{min(index, 3)}"
                tasks.extend([
                    self._task(prefix + "pick", "manipulation", source, prerequisites=(access_id,), domain="ground",
                               capabilities=("manipulator",), continuation=f"order_{index}", object_id=object_id, action="pick"),
                    self._task(prefix + "place", "manipulation", destination, prerequisites=(prefix + "pick",), domain="ground",
                               capabilities=("manipulator",), continuation=f"order_{index}", object_id=object_id, action="place"),
                ])

        return tuple(tasks), tuple(objects)

    def build_episode(self, *, tier: int = 4, condition_id: str | None = None,
                      communication: str | None = None, seed: int = 0) -> EpisodeSpec:
        manifest = self.manifest()
        condition = self._condition(condition_id, tier)
        graph = self.semantic_graph()
        robots = tuple(RobotSpec(row["robot_id"], row["domain"],
                                 f"{row['domain']}::{row['initial_node']}",
                                 speed_mps=float(row["speed_mps"]),
                                 capabilities=tuple(row["capabilities"]))
                       for row in manifest["robot_roster"])
        tasks, objects = self._tasks(tier)
        resources = tuple(ResourceSpec(row["resource_id"],
                                       graph.resolve(row["semantic_label"], "ground")[0],
                                       capacity=int(row["capacity"]),
                                       service_ticks=max(1, round(float(row["service_s"]) / CONTROL_DT_S)))
                          for row in manifest["resources"] if (tier >= 3 or row["resource_id"] == "gate_mutex") or tier == 2)
        deadline = int(manifest["hard_deadline_ticks"][f"t{tier}"])
        return EpisodeSpec(graph=graph, robots=robots, tasks=tasks, resources=resources,
                           horizon_ticks=deadline + 1000, hard_deadline_tick=deadline,
                           dt_s=CONTROL_DT_S, seed=seed,
                           communication_condition=communication or str(condition["communication"]),
                           availability_loss_task=condition.get("availability_loss_task"),
                           objects=objects)

    def contract(self, *, tier: int = 4, condition_id: str | None = None) -> dict[str, Any]:
        manifest = self.manifest(); tier_row = self._tier(tier); condition = self._condition(condition_id, tier)
        deadline = int(manifest["hard_deadline_ticks"][f"t{tier}"])
        return {"scenario_id": self.scenario_id, "scene_key": manifest["scene_key"],
                "task_family": manifest["task_family"], "tier": tier,
                "task_id": tier_row["task_id"], "condition_id": condition["condition_id"],
                "relation_signature": tier_row["relation_signature"],
                "map_revision": manifest["semantic_map"]["revision"],
                "semantic_binding_digest": manifest["semantic_map"]["semantic_binding_digest"],
                "checkpoint_policy": manifest["checkpoint_policy"],
                "communication": condition["communication"], "hard_deadline_tick": deadline,
                "hard_deadline_s": deadline * CONTROL_DT_S,
                "digest": _digest({"manifest": manifest, "tier": tier, "condition": condition["condition_id"]})}

    def run_generic(self, *, tier: int = 4, condition_id: str | None = None,
                    communication: str | None = None, algorithm: Any | None = None,
                    seed: int = 0) -> dict[str, Any]:
        if algorithm is None:
            from benchmark.candy_shop_algorithms import EmosCandyShopAlgorithm
            algorithm = EmosCandyShopAlgorithm()
        runtime = DiagnosticRuntime(self.build_episode(tier=tier, condition_id=condition_id,
                                                       communication=communication, seed=seed), algorithm=algorithm)
        checkpoint = runtime.checkpoint(metadata={"scenario": self.scenario_id, "tier": str(tier), "phase": "spawn"})
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
        """Expose evaluator-confirmed phase milestones for audit/replay."""
        events = list(result.get("events", ()))
        completed = {str(row.get("task_id")): int(row.get("tick", 0))
                     for row in events if row.get("kind") == "task_completed"}
        rows: list[dict[str, Any]] = [{"label": "cp_00_spawn_valid", "tick": 0, "applicable": True}]
        gate_tick = completed.get("gate_open")
        rows.append({"label": "cp_01_gate_open_verified", "tick": gate_tick,
                     "applicable": gate_tick is not None})
        inventory = completed.get("inventory_scan_2", completed.get("inventory_scan_1"))
        rows.append({"label": "cp_02_inventory_observed", "tick": inventory,
                     "applicable": inventory is not None})
        handoff_tasks = [tick for task, tick in completed.items()
                         if task.endswith("_receive") or task.endswith("_release")
                         or task.startswith("access_")]
        rows.append({"label": "cp_03_handoff_complete",
                     "tick": max(handoff_tasks) if handoff_tasks else None,
                     "applicable": bool(handoff_tasks)})
        closeout = int(result.get("completion_tick", result.get("tick", 0))) if result.get("success") else None
        rows.append({"label": "cp_04_order_closeout", "tick": closeout,
                     "applicable": closeout is not None})
        return rows

    @staticmethod
    def diagnostic_metrics(result: Mapping[str, Any]) -> dict[str, Any]:
        events = list(result.get("events", ()))
        placed = {row.get("object_id") for row in events if row.get("kind") == "object_placed"}
        return {"success": bool(result.get("success")), "makespan_s": float(result.get("makespan_s", 0.0)),
                "object_place_success": sorted(x for x in placed if x),
                "resource_wait_s": float(result.get("resource_wait_ticks", 0)) * CONTROL_DT_S,
                "resource_contention_requests": int(result.get("resource_contention_requests", 0)),
                "communication_delay_s": float(result.get("communication_delay_ticks", 0)) * CONTROL_DT_S,
                "communication_loss_bits": int(result.get("communication_loss_bits", 0)),
                "availability_loss": list(result.get("availability_events", ())),
                "checkpoint_replay_equivalent": bool(result.get("replay_equivalent")),
                "D1": bool(result.get("D1_eligible", False)), "D2": False,
                "D3": bool(result.get("D3_eligible", False)), "D4": bool(result.get("availability_events")),
                "D5": False, "D6": False, "paper_score_eligible": False}


__all__ = ["CandyShopScenarioAdapter"]
