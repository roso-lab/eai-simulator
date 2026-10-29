"""Isaac-free factory fire-rescue scenario adapter.

The adapter is intentionally data driven.  Semantic bindings and edge lengths
come from the checked-in factory map; the manifest owns robot capabilities,
registered logical speeds, task stages and the calibrated deadline policy.
Isaac Sim is not started by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from benchmark.hetero_diagnostic_runtime_v1 import (
    CONTROL_DT_S,
    DiagnosticRuntime,
    EpisodeSpec,
    GreedyGoalAlgorithm,
    RobotSpec,
    SemanticEdge,
    SemanticGraph,
    SemanticNode,
    TaskSpec,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "usd/scene/Production and logistics/factory/semantic_map/current"
DEFAULT_MANIFEST = ROOT / "benchmark/tasks/factory/factory_task_manifest.v1.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _edge_length(polyline: list[list[float]] | None,
                 source: tuple[float, float, float],
                 target: tuple[float, float, float]) -> float:
    points = [tuple(float(v) for v in row) for row in (polyline or [source, target])]
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


@dataclass(frozen=True)
class FactoryScenarioAdapter:
    map_root: Path = DEFAULT_MAP
    manifest_path: Path = DEFAULT_MANIFEST

    @property
    def scenario_id(self) -> str:
        return "factory_fire_rescue"

    def manifest(self) -> dict[str, Any]:
        return _load(self.manifest_path)

    def semantic_graph(self) -> SemanticGraph:
        points = _load(self.map_root / "semantic-points.json")
        nodes: list[SemanticNode] = []
        edges: list[SemanticEdge] = []
        for domain, filename in (("ground", "topology.json"), ("aerial", "topology-aerial.json")):
            topology = _load(self.map_root / filename)
            positions = {row["id"]: tuple(float(v) for v in row["position_xyz"]) for row in topology["nodes"]}
            for node_id, position in sorted(positions.items()):
                nodes.append(SemanticNode(f"{domain}::{node_id}", domain, position))
            for edge in topology["edges"]:
                source, target = edge["from"], edge["to"]
                edges.append(SemanticEdge(f"{domain}::{source}", f"{domain}::{target}",
                                          _edge_length(edge.get("polyline_xyz"), positions[source], positions[target])))
        graph = SemanticGraph(nodes, edges)
        for point in points.get("points", ()):
            for binding in point.get("navigation_bindings", ()):
                graph.bind(point["label"], f"{binding['domain']}::{binding['node_id']}")
        return graph

    def _tier(self, tier: int) -> Mapping[str, Any]:
        row = next((item for item in self.manifest()["difficulty_tiers"] if item["difficulty_tier"] == tier), None)
        if row is None:
            raise ValueError("factory difficulty tier must be 1..4")
        return row

    def _condition(self, condition_id: str | None, tier: int) -> Mapping[str, Any]:
        selected = condition_id or self._tier(tier)["default_condition"]
        row = next((item for item in self.manifest()["conditions"] if item["condition_id"] == selected), None)
        if row is None:
            raise ValueError(f"unknown factory condition {selected!r}")
        return row

    @staticmethod
    def _patrol_tasks() -> list[TaskSpec]:
        labels = ("Inspection point_1", "Inspection point_2",
                  "Workstation_1", "Workstation_2", "Workstation_3", "Workstation_4",
                  "Machine_1", "Machine_2")
        return [TaskSpec(
            f"patrol_{index:02d}", "inspection", (label,), required_domain="ground",
            required_capabilities=("patrol_ground",), report_required=True,
            public=True, public_group="patrol")
            for index, label in enumerate(labels, start=1)]

    @staticmethod
    def _incident_tasks(*, mode: str) -> list[TaskSpec]:
        tasks = [
            TaskSpec("fire_event", "event", ("fire_alert",), activation_tasks=("patrol_07",), activation_count=1),
            # Carter is deliberately absent from patrol_ground and is released
            # only after the Machine_1 event.
            TaskSpec("carter_to_block", "incident_navigation", ("Inspection point_2",),
                     prerequisites=("fire_event",), required_domain="ground",
                     required_capabilities=("incident_response",), continuation_group="carter_response",
                     hold_on_completion=True, hold_release_task="help_clear", report_required=True),
            TaskSpec("help_clear", "rescue", ("help",), prerequisites=("fire_event",),
                     required_domain="ground", required_capabilities=("manipulator",), report_required=True),
            TaskSpec("carter_fire_sample", "fire_sample", ("fire",),
                     prerequisites=("fire_event", "carter_to_block", "help_clear"), required_domain="ground",
                     required_capabilities=("incident_response",), continuation_group="carter_response",
                     report_required=True),
            TaskSpec("fire_alarm_press", "button_press", ("Fire alarm",), prerequisites=("fire_event",),
                     required_domain="ground", required_capabilities=("manipulator", "button_press"),
                     report_required=True),
        ]
        if mode == "direct":
            tasks.extend([
                TaskSpec("extinguisher_pick", "manipulation", ("2F Fire Extinguisher",),
                         prerequisites=("fire_event",), required_domain="ground",
                         required_capabilities=("manipulator", "extinguisher_pick"),
                         continuation_group="extinguisher_direct", object_id="extinguisher",
                         object_action="pick", report_required=True),
                TaskSpec("extinguisher_deliver", "manipulation", ("fire",),
                         prerequisites=("extinguisher_pick",), required_domain="ground",
                         required_capabilities=("manipulator", "extinguisher_transport"),
                         continuation_group="extinguisher_direct", object_id="extinguisher",
                         object_action="place", report_required=True),
            ])
        elif mode == "handoff":
            tasks.extend([
                TaskSpec("extinguisher_pick", "manipulation", ("2F Fire Extinguisher",),
                         prerequisites=("fire_event",), required_domain="ground",
                         required_capabilities=("manipulator", "extinguisher_pick"),
                         continuation_group="extinguisher_loader", object_id="extinguisher",
                         object_action="pick", report_required=True),
                TaskSpec("extinguisher_release", "handoff", ("Transfer point",),
                         prerequisites=("extinguisher_pick",), required_domain="ground",
                         required_capabilities=("manipulator",), continuation_group="extinguisher_loader",
                         object_id="extinguisher", object_action="handoff_release", report_required=True),
                TaskSpec("extinguisher_receive", "handoff", ("Transfer point",),
                         prerequisites=("extinguisher_release",), required_domain="ground",
                         required_capabilities=("handoff_receive",), continuation_group="extinguisher_receiver", object_id="extinguisher",
                         object_action="handoff_receive", report_required=True),
                TaskSpec("extinguisher_deliver", "manipulation", ("fire",),
                         prerequisites=("extinguisher_receive",), required_domain="ground",
                         required_capabilities=("extinguisher_transport",), continuation_group="extinguisher_receiver",
                         object_id="extinguisher", object_action="place", report_required=True),
            ])
        else:
            raise ValueError(f"unknown extinguisher mode {mode!r}")
        return tasks

    def build_episode(self, *, tier: int = 4, condition_id: str | None = None,
                      communication: str | None = None, seed: int = 0) -> EpisodeSpec:
        manifest = self.manifest()
        condition = self._condition(condition_id, tier)
        graph = self.semantic_graph()
        roster = tuple(RobotSpec(
            row["robot_id"], row["domain"], graph.resolve(row["initial_point"], row["domain"])[0],
            speed_mps=float(row["speed_mps"]), capabilities=tuple(row["capabilities"]),
        ) for row in manifest["robot_roster"])
        tasks = self._patrol_tasks()
        if tier >= 2:
            tasks.extend(self._incident_tasks(mode=str(condition["extinguisher_mode"])))
        action_ticks = {
            key: max(1, round(float(value) / CONTROL_DT_S))
            for key, value in manifest.get("service_actions", {}).items()
        }
        tasks = [
            TaskSpec(**{**task.__dict__, "service_ticks": action_ticks.get(task.task_id, 0)})
            for task in tasks
        ]
        timing = manifest.get("timing_policy", {})
        deadline_table = timing.get("hard_deadline_ticks", {})
        deadline_key = "t{}:{}".format(tier, condition["condition_id"])
        if deadline_key not in deadline_table:
            raise ValueError("factory hard deadline is not registered for {}".format(deadline_key))
        hard_deadline = int(deadline_table[deadline_key])
        horizon = int(condition.get("horizon_ticks", 120000))
        episode = EpisodeSpec(
            graph=graph, robots=roster, tasks=tuple(tasks), horizon_ticks=horizon,
            hard_deadline_tick=hard_deadline,
            dt_s=CONTROL_DT_S, seed=seed,
            communication_condition=communication or str(condition["communication"]),
            objects=tuple(manifest.get("objects", ())) if tier >= 2 else (),
            report_retry_limit=int(condition.get("report_retry_limit", 8)),
            report_retry_interval_ticks=int(condition.get("report_retry_interval_ticks", 10)),
        )
        self._validate_episode(episode, tier)
        return episode

    @staticmethod
    def _validate_episode(episode: EpisodeSpec, tier: int) -> None:
        if len(episode.robots) != 4 or any(robot.domain != "ground" for robot in episode.robots):
            raise ValueError("factory roster must contain four ground robots")
        if tier >= 2 and "fire_event" not in {task.task_id for task in episode.tasks}:
            raise ValueError("fire event missing for tier >= 2")

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
            "checkpoint_policy": manifest["checkpoint_policy"],
            "communication": condition["communication"], "extinguisher_mode": condition["extinguisher_mode"],
            "hard_deadline_tick": int(manifest["timing_policy"]["hard_deadline_ticks"]["t{}:{}".format(tier, condition["condition_id"])]),
            "hard_deadline_s": int(manifest["timing_policy"]["hard_deadline_ticks"]["t{}:{}".format(tier, condition["condition_id"])]) * CONTROL_DT_S,
            "digest": _digest({"manifest": manifest, "tier": tier, "condition": condition["condition_id"]}),
        }

    def run_generic(self, *, tier: int = 4, condition_id: str | None = None,
                    communication: str | None = None, algorithm: Any | None = None,
                    seed: int = 0) -> dict[str, Any]:
        if algorithm is None:
            from benchmark.factory_algorithms import EmosFactoryAlgorithm
            algorithm = EmosFactoryAlgorithm()
        runtime = DiagnosticRuntime(self.build_episode(tier=tier, condition_id=condition_id,
                                                       communication=communication, seed=seed),
                                    algorithm=algorithm)
        checkpoint = runtime.checkpoint(metadata={"schema": runtime.schema_version, "tick": "0"})
        result = runtime.run()
        result["checkpoint"] = checkpoint
        result["scenario"] = self.contract(tier=tier, condition_id=condition_id)
        return result


__all__ = ["FactoryScenarioAdapter"]
