"""Frozen city_02 scenario adapter for the Isaac-free diagnostic runtime.

The adapter is deliberately data driven: topology and semantic bindings come
from the published city_02 map, while task/event/object semantics come from
``city_02_task_manifest.v1.json``.  Isaac Sim is not started by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from benchmark.hetero_diagnostic_runtime_v1 import (
    CONTROL_DT_S, DiagnosticRuntime, EpisodeSpec, GreedyGoalAlgorithm,
    RobotSpec, SemanticEdge, SemanticGraph, SemanticNode, TaskSpec,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "usd/scene/Service and operations/city_02/semantic_map/current"
DEFAULT_MANIFEST = ROOT / "benchmark/tasks/city_02/city_02_task_manifest.v1.json"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _edge_length(polyline: list[list[float]] | None, source: tuple[float, float, float], target: tuple[float, float, float]) -> float:
    points = [tuple(float(v) for v in row) for row in (polyline or [source, target])]
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


@dataclass(frozen=True)
class City02ScenarioAdapter:
    map_root: Path = DEFAULT_MAP
    manifest_path: Path = DEFAULT_MANIFEST

    @property
    def scenario_id(self) -> str:
        return "city_02"

    def manifest(self) -> dict[str, Any]:
        return _load(self.manifest_path)

    def semantic_graph(self) -> SemanticGraph:
        points_doc = _load(self.map_root / "semantic-points.json")
        nodes: list[SemanticNode] = []
        edges: list[SemanticEdge] = []
        for domain, filename in (("ground", "topology.json"), ("aerial", "topology-aerial.json")):
            topology = _load(self.map_root / filename)
            positions = {row["id"]: tuple(float(v) for v in row["position_xyz"]) for row in topology["nodes"]}
            for node_id, position in sorted(positions.items()):
                nodes.append(SemanticNode(f"{domain}::{node_id}", domain, position))
            for edge in topology["edges"]:
                source, target = edge["from"], edge["to"]
                edges.append(SemanticEdge(f"{domain}::{source}", f"{domain}::{target}", _edge_length(edge.get("polyline_xyz"), positions[source], positions[target])))
        graph = SemanticGraph(nodes, edges)
        for point in points_doc.get("points", ()):
            for binding in point.get("navigation_bindings", ()):
                graph.bind(point["label"], f"{binding['domain']}::{binding['node_id']}")
        return graph

    def _tier(self, tier: int) -> Mapping[str, Any]:
        row = next((item for item in self.manifest()["difficulty_tiers"] if item["difficulty_tier"] == tier), None)
        if row is None:
            raise ValueError(f"city_02 tier must be one of 1..4, got {tier}")
        return row

    def _condition(self, condition_id: str | None, tier: int) -> Mapping[str, Any]:
        selected = condition_id or self._tier(tier)["default_condition"]
        row = next((item for item in self.manifest()["conditions"] if item["condition_id"] == selected), None)
        if row is None:
            raise ValueError(f"unknown city_02 condition {selected!r}")
        return row

    @staticmethod
    def _patrol_tasks(tier: int, regions: tuple[Mapping[str, Any], ...]) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        if tier == 1:
            return [TaskSpec(
                f"coverage_{region['area_id']}", "coverage", (region["area_id"],),
                report_required=True,
            ) for region in regions]
        for index in range(1, 10):
            # The two possible Inspection point_9 bindings are resolved by the
            # runtime to the nearest reachable binding for the assigned robot.
            tasks.append(TaskSpec(
                f"ground_patrol_{index}", "inspection", (f"Inspection point_{index}",),
                required_domain="ground", report_required=True,
                hold_on_completion=index in (1, 9),
                hold_release_task="box_place" if index == 1 else ("warning_place" if index == 9 else None),
            ))
        for index in range(1, 13):
            tasks.append(TaskSpec(
                f"aerial_patrol_{index}", "inspection", (f"build_{index:02d}",),
                required_domain="aerial", report_required=True,
                hold_on_completion=index == 10, hold_release_task="box_place" if index == 10 else None,
            ))
        return tasks

    @staticmethod
    def _event_and_manipulation_tasks(tier: int) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        if tier >= 2:
            tasks.extend([
                TaskSpec("manhole_event", "event", ("manhole_opened",), activation_tasks=("ground_patrol_9",), activation_count=1),
                TaskSpec("warning_pick", "manipulation", ("Warning sign",), prerequisites=("manhole_event",), required_domain="ground", required_capabilities=("manipulator",), continuation_group="warning", object_id="warning_sign", object_action="pick", report_required=True),
                TaskSpec("warning_place", "manipulation", ("Manhole cover",), prerequisites=("warning_pick",), required_domain="ground", required_capabilities=("manipulator",), continuation_group="warning", object_id="warning_sign", object_action="place", report_required=True),
            ])
        if tier >= 3:
            tasks.extend([
                TaskSpec("box_event", "event", ("box_found",), activation_tasks=("ground_patrol_1", "aerial_patrol_10"), activation_count=1),
                TaskSpec("box_pick", "manipulation", ("box",), prerequisites=("box_event",), required_domain="ground", required_capabilities=("manipulator",), continuation_group="box", object_id="box", object_action="pick", report_required=True),
                TaskSpec("box_place", "manipulation", ("Trash can",), prerequisites=("box_pick",), required_domain="ground", required_capabilities=("manipulator",), continuation_group="box", object_id="box", object_action="place", report_required=True),
            ])
        return tasks

    def build_episode(self, *, tier: int = 4, condition_id: str | None = None,
                      communication: str | None = None, seed: int = 0) -> EpisodeSpec:
        manifest = self.manifest()
        condition = self._condition(condition_id, tier)
        graph = self.semantic_graph()
        regions = tuple(manifest["coverage"]["regions"])
        for region in regions:
            anchors = list(region.get("ground_labels", ())) + list(region.get("aerial_labels", ()))
            for anchor in anchors:
                for node in graph.resolve(anchor):
                    graph.bind(region["area_id"], node)
        robots = tuple(RobotSpec(
            row["robot_id"], row["domain"], graph.resolve(row["initial_point"], row["domain"])[0],
            speed_mps=float(row["speed_mps"]), capabilities=tuple(row["capabilities"]),
        ) for row in manifest["robot_roster"])
        tasks = self._patrol_tasks(tier, regions) + self._event_and_manipulation_tasks(tier)
        task_ids = {task.task_id for task in tasks}
        # Holds only make sense when the corresponding responder branch is
        # present in the selected difficulty tier.
        for index, task in enumerate(tasks):
            if tier == 1:
                continue
            if task.task_id == "ground_patrol_9" and "warning_place" not in task_ids:
                tasks[index] = replace(task, hold_on_completion=False, hold_release_task=None)
            elif task.task_id == "ground_patrol_1" and "box_place" not in task_ids:
                tasks[index] = replace(task, hold_on_completion=False, hold_release_task=None)
            elif task.task_id == "aerial_patrol_10" and "box_place" not in task_ids:
                tasks[index] = replace(task, hold_on_completion=False, hold_release_task=None)
        episode = EpisodeSpec(
            graph=graph, robots=robots, tasks=tuple(tasks),
            horizon_ticks=300000, dt_s=CONTROL_DT_S, seed=seed,
            communication_condition=communication or str(condition["communication"]),
            objects=tuple(manifest["objects"]),
        )
        self._validate_episode(episode, tier)
        return episode

    @staticmethod
    def _validate_episode(episode: EpisodeSpec, tier: int) -> None:
        if len(episode.robots) != 5 or sum(r.domain == "ground" for r in episode.robots) != 4:
            raise ValueError("city_02 roster must contain four ground robots and one Pegasus")
        for task in episode.tasks:
            if task.required_capabilities and not task.required_domain:
                raise ValueError(f"capability task {task.task_id} must declare a domain")
        if tier >= 2 and "manhole_event" not in {task.task_id for task in episode.tasks}:
            raise ValueError("manhole event missing for tier >= 2")

    def contract(self, *, tier: int = 4, condition_id: str | None = None) -> dict[str, Any]:
        manifest = self.manifest()
        condition = self._condition(condition_id, tier)
        return {
            "scenario_id": self.scenario_id, "scene_key": manifest["scene_key"],
            "task_family": manifest["task_family"], "tier": tier,
            "task_id": self._tier(tier)["task_id"], "condition_id": condition["condition_id"],
            "relation_signature": self._tier(tier)["relation_signature"],
            "map_revision": manifest["semantic_map"]["revision"],
            "semantic_binding_digest": manifest["semantic_map"]["semantic_binding_digest"],
            "checkpoint_policy": manifest["checkpoints"], "communication": condition["communication"],
            "digest": _digest({"manifest": manifest, "tier": tier, "condition": condition["condition_id"]}),
        }

    def run_generic(self, *, tier: int = 4, condition_id: str | None = None,
                    communication: str | None = None, algorithm: Any | None = None,
                    seed: int = 0) -> dict[str, Any]:
        from benchmark.city_02_algorithms import EmosCity02Algorithm
        runtime = DiagnosticRuntime(self.build_episode(tier=tier, condition_id=condition_id,
                                                       communication=communication, seed=seed),
                                    algorithm=algorithm or EmosCity02Algorithm())
        result = runtime.run()
        result["scenario"] = self.contract(tier=tier, condition_id=condition_id)
        result["diagnostic_metrics"] = self.diagnostic_metrics(result)
        return result

    def diagnostic_metrics(self, result: Mapping[str, Any]) -> dict[str, Any]:
        manifest = self.manifest()
        events = list(result.get("events", ()))
        completed_targets = {row.get("target") for row in events if row.get("kind") == "task_completed"}
        ground = {f"Inspection point_{i}" for i in range(1, 10)}
        aerial = {f"build_{i:02d}" for i in range(1, 13)}
        covered_areas = 0
        completed_area_ids = {target for target in completed_targets if isinstance(target, str) and target.startswith("area_")}
        for region in manifest["coverage"]["regions"]:
            members = set(region.get("ground_labels", ())) | set(region.get("aerial_labels", ()))
            if region["area_id"] in completed_area_ids or members & completed_targets:
                covered_areas += 1
        def first(kind: str, predicate=lambda row: True):
            rows = [row for row in events if row.get("kind") == kind and predicate(row)]
            return min((int(row["tick"]) * CONTROL_DT_S for row in rows), default=None)
        holds = [row for row in events if row.get("kind") == "robot_hold_started"]
        releases = [row for row in events if row.get("kind") == "robot_hold_released"]
        hold_ticks = 0
        for start in holds:
            end = next((row["tick"] for row in releases if row.get("robot_id") == start.get("robot_id") and row.get("release_task") == start.get("release_task") and row["tick"] >= start["tick"]), None)
            hold_ticks += (end - start["tick"]) if end is not None else 0
        objects = {row.get("object_id"): row for row in events if row.get("kind") == "object_placed"}
        return {
            "success": bool(result.get("success")), "makespan_s": float(result.get("makespan_s", 0.0)),
            "patrol_ground_fraction": len(ground & completed_targets) / len(ground),
            "patrol_aerial_fraction": len(aerial & completed_targets) / len(aerial),
            "xy_area_coverage_fraction": covered_areas / len(manifest["coverage"]["regions"]),
            "detection_latency_s": {"manhole": first("incident_triggered", lambda row: row.get("task_id") == "manhole_event"), "box": first("incident_triggered", lambda row: row.get("task_id") == "box_event")},
            "notification_latency_s": (float(result.get("communication_delay_ticks", 0)) * CONTROL_DT_S),
            "discoverer_hold_s": hold_ticks * CONTROL_DT_S,
            "object_place_success": {"warning_sign": "warning_sign" in objects, "box": "box" in objects},
            "object_placed_count": len(objects),
            "duplicate_trigger_suppression": sum(1 for row in events if row.get("kind") == "incident_triggered" and row.get("task_id") in {"manhole_event", "box_event"}) == len({row.get("task_id") for row in events if row.get("kind") == "incident_triggered"}),
            "checkpoint_replay_equivalent": bool(result.get("replay_equivalent")),
            "capability_invalid_assignments": sum(1 for row in events if row.get("kind") == "invalid_assignment" and row.get("reason") == "missing_required_capability"),
            "paper_score_eligible": False,
        }

    def validate_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        errors = []
        metrics = self.diagnostic_metrics(result)
        if result.get("scenario", {}).get("scenario_id") not in (None, self.scenario_id):
            errors.append("scenario_id_mismatch")
        for key in ("patrol_ground_fraction", "patrol_aerial_fraction", "xy_area_coverage_fraction"):
            if not 0.0 <= float(metrics[key]) <= 1.0:
                errors.append(f"{key}_out_of_range")
        return {"valid": not errors, "errors": errors}


__all__ = ["City02ScenarioAdapter"]
