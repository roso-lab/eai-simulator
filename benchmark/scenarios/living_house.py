"""Isaac-free living-house benchmark adapter.

The adapter consumes the checked-in semantic map and turns the household
scenario into a deterministic typed task graph.  High table-top points are
represented by an explicit interaction-pose alias at ``Manipulation point1``;
the source map remains unchanged and the receipt records that the alias is a
logical surface witness pending a robot-validated 3-D interaction test.
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
    GateSpec, ResourceSpec, RobotSpec, SemanticEdge, SemanticGraph, SemanticNode, TaskSpec,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "usd/scene/Living and public space/house/semantic_map/current"
DEFAULT_MANIFEST = ROOT / "benchmark/tasks/living_house/living_house_task_manifest.v1.json"


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
class LivingHouseScenarioAdapter:
    map_root: Path = DEFAULT_MAP
    manifest_path: Path = DEFAULT_MANIFEST

    @property
    def scenario_id(self) -> str:
        return "living_house"

    def manifest(self) -> dict[str, Any]:
        return _load(self.manifest_path)

    def _validate_map(self) -> None:
        manifest = self.manifest()
        expected = manifest["semantic_map"]
        actual = _load(self.map_root / "manifest.json")
        if actual.get("scene_key") != manifest["scene_key"]:
            raise ValueError("living_house semantic-map scene_key mismatch")
        for key in ("revision", "semantic_binding_digest"):
            if actual.get(key) != expected[key]:
                raise ValueError(f"living_house semantic-map {key} mismatch")

    def semantic_graph(self) -> SemanticGraph:
        self._validate_map()
        points = _load(self.map_root / "semantic-points.json")["points"]
        nodes: list[SemanticNode] = []
        edges: list[SemanticEdge] = []
        for domain, filename in (("ground", "topology.json"), ("aerial", "topology-aerial.json")):
            topology = _load(self.map_root / filename)
            positions = {row["id"]: tuple(float(v) for v in row["position_xyz"]) for row in topology["nodes"]}
            for node_id, position in sorted(positions.items()):
                nodes.append(SemanticNode(f"{domain}::{node_id}", domain, position))
            for edge in topology["edges"]:
                traversal_mode = str(edge.get("traversal_mode", "walk"))
                required_caps = tuple(cap for cap in edge.get("required_controller_capabilities", ())
                                      if cap != "stair_capable")
                edges.append(SemanticEdge(f"{domain}::{edge['from']}", f"{domain}::{edge['to']}",
                                          _edge_length(edge, positions),
                                          bidirectional=not bool(edge.get("directed", False)),
                                          traversal_mode=traversal_mode,
                                          required_controller_capabilities=required_caps))
        graph = SemanticGraph(nodes, edges)
        for point in points:
            for binding in point.get("navigation_bindings", ()):
                graph.bind(point["label"], f"{binding['domain']}::{binding['node_id']}")
        # Surface points are three-dimensional manipulation locations.  Their
        # logical navigation witness is the Pepper arm pose, not a fabricated
        # ground edge between disconnected semantic components.
        surface_pose = graph.resolve("Manipulation point1", "ground")[0]
        for label in self.manifest()["surface_interaction"]["logical_bindings"]:
            graph.bind(label, surface_pose)
        return graph

    def _tier(self, tier: int) -> Mapping[str, Any]:
        row = next((item for item in self.manifest()["difficulty_tiers"] if int(item["difficulty_tier"]) == int(tier)), None)
        if row is None:
            raise ValueError("living_house difficulty tier must be 1..4")
        return row

    def _condition(self, tier: int, condition_id: str | None) -> Mapping[str, Any]:
        selected = condition_id or self._tier(tier)["default_condition"]
        row = next((item for item in self.manifest()["conditions"] if item["condition_id"] == selected), None)
        if row is None:
            raise ValueError(f"unknown living_house condition {selected!r}")
        return row

    def d1_infeasible_certificate(self) -> dict[str, Any]:
        """Return the contract-level proof for the wrong-team D1 case.

        D1 deliberately contains the previously used team (M20+D1 and
        Carter+D1) and only asks for a two-holder table transport down the
        stairs.  Carter can carry a table but has no stair capability, while
        M20 can do both.  Consequently only one distinct holder is eligible
        for a two-holder cooperative task.  This is a contract-level
        ``team-state-infeasible`` result; a timeout must not be used as its
        proof.
        """
        manifest = self.manifest()
        case_id = "D1_infeasible_wrong_team_table"
        case = manifest["diagnostic_cases"][case_id]
        roster = manifest["diagnostic_robot_roster"]
        required = set(case.get("required_capabilities", ("table_carry", "stair_down")))
        eligible = [row["robot_id"] for row in roster if required.issubset(set(row.get("capabilities", ()) ))]
        required_holders = int(case.get("required_distinct_holders", 2))
        return {
            "case_id": case_id,
            **case,
            "eligible_distinct_holders": len(eligible),
            "eligible_robot_ids": eligible,
            "required_distinct_holders": required_holders,
            "infeasibility_reason": "carter_no_stair_capability",
            "proof": f"eligible_distinct_holders={len(eligible)} < required_distinct_holders={required_holders}",
            "expected_action": "not-deployable_safe-stop",
        }

    def build_d1_infeasible_episode(self, *, seed: int = 0) -> EpisodeSpec:
        graph = self.semantic_graph(); manifest = self.manifest()
        roster = []
        for row in manifest["diagnostic_robot_roster"]:
            roster.append(RobotSpec(
                row["robot_id"], row["domain"],
                graph.resolve(row["initial_point"], row["domain"])[0],
                speed_mps=float(row["speed_mps"]),
                capabilities=tuple(row["capabilities"]),
                one_shot_capabilities=tuple(row.get("one_shot_capabilities", ())),
            ))
        gates = (GateSpec("door_3", graph.resolve("door_in_3", "ground")[0], graph.resolve("door_out_3", "ground")[0]),)
        return EpisodeSpec(
            graph=graph,
            robots=tuple(roster),
            tasks=tuple(self._table_tasks()),
            gates=gates,
            horizon_ticks=5000,
            hard_deadline_tick=4000,
            dt_s=CONTROL_DT_S,
            seed=seed,
        )

    @staticmethod
    def _table_tasks() -> list[TaskSpec]:
        """Two distinct table holders on the logical downstairs route.

        The map has no independent tabletop grasp nodes for this diagnostic
        payload.  The source and route witness are declared in the manifest;
        the runtime task itself records the two required holders and the
        destination at ``chair_new``.
        """
        common = dict(
            kind="cooperative_carry",
            targets=("chair_new",),
            required_domain="ground",
            required_capabilities=("table_carry", "stair_down"),
            distinct_group="table_holders",
            cooperative_group="table_downstairs",
            report_required=True,
        )
        return [
            TaskSpec("table_carry_left", **common),
            TaskSpec("table_carry_right", **common),
        ]

    @staticmethod
    def _chair_tasks() -> list[TaskSpec]:
        common = dict(required_domain="ground", required_capabilities=("chair_grasp",), report_required=True)
        return [
            TaskSpec("chair_grasp_left", "manipulation", ("chair_left",), distinct_group="chair_graspers", continuation_group="chair_left_holder", hold_until_prerequisites=("chair_grasp_left", "chair_grasp_right"), **common),
            TaskSpec("chair_grasp_right", "manipulation", ("chair_right",), distinct_group="chair_graspers", continuation_group="chair_right_holder", hold_until_prerequisites=("chair_grasp_left", "chair_grasp_right"), **common),
            TaskSpec("chair_carry_left", "cooperative_carry", ("door_in_3",), prerequisites=("chair_grasp_left", "chair_grasp_right"), required_domain="ground", required_capabilities=("chair_carry",), continuation_group="chair_left_holder", cooperative_group="chair_carry", report_required=True),
            TaskSpec("chair_carry_right", "cooperative_carry", ("door_in_3",), prerequisites=("chair_grasp_left", "chair_grasp_right"), required_domain="ground", required_capabilities=("chair_carry",), continuation_group="chair_right_holder", cooperative_group="chair_carry", report_required=True),
            # This is an unladen door-side access opportunity.  Any ground
            # robot may execute it; the runtime also opens the gate whenever
            # an unladen robot reaches either side, so this is not a special
            # Carter/door-operator role and is not a per-door checkpoint.
            TaskSpec("chair_door_open", "door_open", ("door_in_3",), prerequisites=("chair_grasp_left", "chair_grasp_right"), required_domain="ground", required_capabilities=(), service_ticks=0, report_required=False, required_for_success=False, gate_id="door_3", gate_action="open"),
            TaskSpec("chair_transit_left", "door_transit", ("door_out_3",), prerequisites=("chair_door_open",), required_domain="ground", required_capabilities=("chair_carry",), continuation_group="chair_left_holder", cooperative_group="chair_transit", report_required=True, gate_id="door_3", gate_action="transit"),
            TaskSpec("chair_transit_right", "door_transit", ("door_out_3",), prerequisites=("chair_door_open",), required_domain="ground", required_capabilities=("chair_carry",), continuation_group="chair_right_holder", cooperative_group="chair_transit", report_required=True, gate_id="door_3", gate_action="transit"),
            TaskSpec("chair_place_left", "cooperative_place", ("chair_new",), prerequisites=("chair_transit_left", "chair_transit_right"), required_domain="ground", required_capabilities=("chair_carry",), continuation_group="chair_left_holder", cooperative_group="chair_place", report_required=True),
            TaskSpec("chair_place_right", "cooperative_place", ("chair_new",), prerequisites=("chair_transit_left", "chair_transit_right"), required_domain="ground", required_capabilities=("chair_carry",), continuation_group="chair_right_holder", cooperative_group="chair_place", report_required=True),
        ]

    @staticmethod
    def _pot_tasks() -> list[TaskSpec]:
        return [
            TaskSpec("pot_pick", "object_pick", ("Pot",), required_domain="aerial", required_capabilities=("pot_pick",), continuation_group="pot_carrier", object_id="pot", object_action="pick", report_required=True),
            TaskSpec("pot_place", "object_place", ("Table",), prerequisites=("pot_pick",), required_domain="aerial", required_capabilities=("aerial_payload",), continuation_group="pot_carrier", object_id="pot", object_action="place", report_required=True),
        ]

    @staticmethod
    def _transfer_tasks(object_id: str, *, mode: str, sink_cycle: bool) -> list[TaskSpec]:
        required_receiver = ("handoff_receive", "outdoor_transport")
        if sink_cycle and object_id == "fruit":
            required_receiver = ("handoff_receive", "outdoor_transport", "manipulator")
        tasks = [
            TaskSpec(f"{object_id}_surface_pick", "surface_pick", ("Manipulation point1",), required_domain="ground", required_capabilities=("arm_manipulation",), continuation_group=f"{object_id}_pepper", object_id=object_id, object_action="pick", report_required=True),
        ]
        if mode == "air":
            tasks.extend([
                TaskSpec(f"{object_id}_air_release", "handoff_release", ("air_trans",), prerequisites=(f"{object_id}_surface_pick",), required_domain="ground", required_capabilities=("arm_manipulation",), continuation_group=f"{object_id}_pepper", object_id=object_id, object_action="handoff_release", report_required=True),
                TaskSpec(f"{object_id}_air_receive", "handoff_receive", ("air grasping point",), prerequisites=(f"{object_id}_air_release",), required_domain="aerial", required_capabilities=("aerial_handoff",), continuation_group=f"{object_id}_air_receiver", object_id=object_id, object_action="handoff_receive", report_required=True),
                TaskSpec(f"{object_id}_air_place", "object_place", ("Table",), prerequisites=(f"{object_id}_air_receive",), required_domain="aerial", required_capabilities=("aerial_payload",), continuation_group=f"{object_id}_air_receiver", object_id=object_id, object_action="place", report_required=True),
            ])
        else:
            transfer = "trans1" if object_id == "fruit" else "trans2"
            tasks.extend([
                TaskSpec(f"{object_id}_release", "handoff_release", (transfer,), prerequisites=(f"{object_id}_surface_pick",), required_domain="ground", required_capabilities=("arm_manipulation",), continuation_group=f"{object_id}_pepper", object_id=object_id, object_action="handoff_release", report_required=True),
                TaskSpec(f"{object_id}_receive", "handoff_receive", (transfer,), prerequisites=(f"{object_id}_release",), required_domain="ground", required_capabilities=required_receiver, continuation_group=f"{object_id}_receiver", object_id=object_id, object_action="handoff_receive", report_required=True),
                TaskSpec(f"{object_id}_deliver", "object_place", ("chair_new",), prerequisites=(f"{object_id}_receive",), required_domain="ground", required_capabilities=("outdoor_transport",), continuation_group=f"{object_id}_receiver", object_id=object_id, object_action="place", report_required=True),
            ])
            if sink_cycle and object_id == "fruit":
                tasks.extend([
                    TaskSpec("fruit_sink_pick", "sink_pick", ("chair_new",), prerequisites=("fruit_deliver",), required_domain="ground", required_capabilities=("manipulator",), continuation_group="fruit_sink_owner", object_id="fruit", object_action="pick", report_required=True),
                    TaskSpec("fruit_sink_delivery", "sink_delivery", ("Sink",), prerequisites=("fruit_sink_pick",), required_domain="ground", required_capabilities=("manipulator",), continuation_group="fruit_sink_owner", object_id="fruit", object_action="place_intermediate", report_required=True),
                    TaskSpec("fruit_sink_return_pick", "sink_return_pick", ("Sink",), prerequisites=("fruit_sink_delivery",), required_domain="ground", required_capabilities=("manipulator",), continuation_group="fruit_sink_owner", object_id="fruit", object_action="pick", report_required=True),
                    TaskSpec("fruit_sink_return", "sink_return", ("chair_new",), prerequisites=("fruit_sink_return_pick",), required_domain="ground", required_capabilities=("manipulator",), continuation_group="fruit_sink_owner", object_id="fruit", object_action="place", report_required=True),
                ])
        return tasks

    def build_episode(self, *, tier: int = 4, condition_id: str | None = None, communication: str | None = None, seed: int = 0) -> EpisodeSpec:
        manifest = self.manifest(); tier_row = self._tier(tier); condition = self._condition(tier, condition_id)
        graph = self.semantic_graph()
        roster = tuple(RobotSpec(row["robot_id"], row["domain"], graph.resolve(row["initial_point"], row["domain"])[0], speed_mps=float(row["speed_mps"]), capabilities=tuple(row["capabilities"]), one_shot_capabilities=tuple(row.get("one_shot_capabilities", ()))) for row in manifest["robot_roster"])
        tasks = self._chair_tasks() + self._pot_tasks()
        mode = str(tier_row["transfer_mode"])
        if mode == "ground":
            tasks.extend(self._transfer_tasks("fruit", mode="ground", sink_cycle=False)); tasks.extend(self._transfer_tasks("bowl", mode="ground", sink_cycle=False))
        elif mode == "air":
            tasks.extend(self._transfer_tasks("fruit", mode="air", sink_cycle=False)); tasks.extend(self._transfer_tasks("bowl", mode="air", sink_cycle=False))
        elif mode == "mixed":
            tasks.extend(self._transfer_tasks("fruit", mode="ground", sink_cycle=bool(tier_row["sink_cycle"]))); tasks.extend(self._transfer_tasks("bowl", mode="air", sink_cycle=False))
        objects: list[Mapping[str, Any]] = [
            {"object_id": "pot", "source": "Pot", "destination": "Table", "source_semantic_label": "Pot"},
        ]
        if mode in {"ground", "mixed"}:
            objects.append({"object_id": "fruit", "source": "Manipulation point1", "destination": "chair_new", "source_semantic_label": "fruit"})
        if mode == "ground":
            objects.append({"object_id": "bowl", "source": "Manipulation point1", "destination": "chair_new", "source_semantic_label": "bowl"})
        elif mode == "air":
            objects.extend([
                {"object_id": "fruit", "source": "Manipulation point1", "destination": "Table", "source_semantic_label": "fruit"},
                {"object_id": "bowl", "source": "Manipulation point1", "destination": "Table", "source_semantic_label": "bowl"},
            ])
        elif mode == "mixed":
            objects.append({"object_id": "bowl", "source": "Manipulation point1", "destination": "Table", "source_semantic_label": "bowl"})
        timing = manifest["timing_policy"]
        candidates = timing.get("logical_candidate_deadline_ticks", {})
        fallback = timing["provisional_deadline_ticks"]
        key = f"t{tier}:{condition['condition_id']}"
        deadline = int(candidates.get(key, candidates.get(f"t{tier}:C0", fallback.get(f"t{tier}:C0", 62000))))
        gates = (GateSpec("door_3", graph.resolve("door_in_3", "ground")[0], graph.resolve("door_out_3", "ground")[0]),)
        # Availability loss is injected only after the last aerial obligation
        # for the selected tier reports complete.  This preserves a solvable
        # episode while making C3 a real dynamic-reconfiguration condition.
        availability_loss_task = None
        availability_loss_after_tasks: tuple[str, ...] = ()
        if bool(condition.get("availability_loss")):
            availability_loss_task = "pot_place"
            if mode in {"air", "mixed"}:
                availability_loss_task = None
                availability_loss_after_tasks = ["pot_place", "bowl_air_place"]
                if mode == "air":
                    availability_loss_after_tasks.insert(1, "fruit_air_place")
                availability_loss_after_tasks = tuple(availability_loss_after_tasks)
        episode = EpisodeSpec(graph=graph, robots=roster, tasks=tuple(tasks), resources=(), gates=gates, horizon_ticks=deadline + 1000, hard_deadline_tick=deadline, dt_s=CONTROL_DT_S, seed=seed, communication_condition=communication or str(condition["communication"]), objects=tuple(objects), availability_loss_task=availability_loss_task, availability_loss_after_tasks=availability_loss_after_tasks)
        return episode

    def contract(self, *, tier: int = 4, condition_id: str | None = None) -> dict[str, Any]:
        manifest = self.manifest(); tier_row = self._tier(tier); condition = self._condition(tier, condition_id)
        key = f"t{tier}:{condition['condition_id']}"; timing = manifest["timing_policy"]; deadline = int(timing.get("logical_candidate_deadline_ticks", {}).get(key, timing["provisional_deadline_ticks"].get(f"t{tier}:C0", 62000)))
        return {"scenario_id": self.scenario_id, "scene_key": manifest["scene_key"], "task_family": manifest["task_family"], "tier": tier, "task_id": tier_row["task_id"], "condition_id": condition["condition_id"], "relation_signature": tier_row["relation_signature"], "map_revision": manifest["semantic_map"]["revision"], "semantic_binding_digest": manifest["semantic_map"]["semantic_binding_digest"], "surface_interaction": manifest["surface_interaction"], "checkpoint_policy": manifest["checkpoint_policy"], "hard_deadline_tick": deadline, "hard_deadline_s": deadline * CONTROL_DT_S, "paper_score_eligible": False, "digest": _digest({"manifest": manifest, "tier": tier, "condition": condition["condition_id"]})}

    def run_generic(self, *, tier: int = 4, condition_id: str | None = None, communication: str | None = None, algorithm: Any | None = None, seed: int = 0) -> dict[str, Any]:
        runtime = DiagnosticRuntime(self.build_episode(tier=tier, condition_id=condition_id, communication=communication, seed=seed), algorithm=algorithm or GreedyGoalAlgorithm())
        checkpoint = runtime.checkpoint(metadata={"schema": runtime.schema_version, "scenario": self.scenario_id, "tier": str(tier)})
        result = runtime.run(); result["checkpoint"] = checkpoint; result["scenario"] = self.contract(tier=tier, condition_id=condition_id)
        result["replay"] = runtime.replay(checkpoint); result["replay_equivalent"] = bool(result["replay"].get("replay_equivalent"))
        result["checkpoints"] = self.milestone_checkpoints(result)
        result["living_house_metrics"] = self.diagnostic_metrics(result)
        return result

    @staticmethod
    def diagnostic_metrics(result: Mapping[str, Any]) -> dict[str, Any]:
        events = list(result.get("events", ()))
        by_group = {}
        for event in events:
            if event.get("kind") == "cooperative_service_started":
                by_group.setdefault(str(event.get("group")), []).append(event)
        sync_gaps = []
        group_tasks = {"chair_carry": ("chair_carry_left", "chair_carry_right"),
                       "chair_transit": ("chair_transit_left", "chair_transit_right"),
                       "chair_place": ("chair_place_left", "chair_place_right")}
        for group, task_ids in group_tasks.items():
            assigned = {str(e.get("task_id")): str(e.get("robot_id")) for e in events if e.get("kind") == "task_assigned" and e.get("task_id") in task_ids}
            target_by_task = {task_id: next((str(e.get("target")) for e in events if e.get("kind") == "task_assigned" and e.get("task_id") == task_id), None) for task_id in task_ids}
            arrivals = []
            for task_id in task_ids:
                robot_id = assigned.get(task_id); target = target_by_task.get(task_id)
                ticks = [int(e["tick"]) for e in events if e.get("kind") == "goal_reached" and e.get("robot_id") == robot_id and e.get("target") == target]
                if ticks:
                    arrivals.append(ticks[0])
            if len(arrivals) == len(task_ids) and group in by_group:
                sync_gaps.append(max(arrivals) - min(arrivals))
        opened = [int(e["tick"]) for e in events if e.get("kind") == "gate_opened"]
        closed = [int(e["tick"]) for e in events if e.get("kind") == "gate_closed"]
        return {
            "success": bool(result.get("success")),
            "makespan_s": float(result.get("makespan_s", 0.0)),
            "door_open_count": len(opened),
            "door_close_count": len(closed),
            "door_open_to_close_s": ((closed[0] - opened[0]) * CONTROL_DT_S if opened and closed else None),
            "cooperative_sync_gap_s": [abs(gap) * CONTROL_DT_S for gap in sync_gaps],
            "handoff_count": sum(e.get("kind") == "handoff_accept" for e in events),
            "invalid_assignment_count": sum(e.get("kind") == "invalid_assignment" for e in events),
            "replay_equivalent": bool(result.get("replay_equivalent")),
            "paper_score_eligible": False,
        }

    @staticmethod
    def milestone_checkpoints(result: Mapping[str, Any]) -> list[dict[str, Any]]:
        events = list(result.get("events", ())); rows = [{"label": "cp_00_spawn_valid", "tick": 0}]
        def completed(*ids: str) -> int | None:
            ticks = [int(e["tick"]) for e in events if e.get("kind") == "task_completed" and e.get("task_id") in ids]
            return max(ticks) if ticks else None
        if any(e.get("kind") == "task_announced" for e in events): rows.append({"label": "cp_01_parallel_release", "tick": min(int(e["tick"]) for e in events if e.get("kind") == "task_announced")})
        for label, ids in (("cp_02_chair_grasps", ("chair_grasp_left", "chair_grasp_right")), ("cp_03_chair_at_door_in", ("chair_carry_left", "chair_carry_right")), ("cp_04_chair_through_door", ("chair_transit_left", "chair_transit_right")), ("cp_05_chair_placed", ("chair_place_left", "chair_place_right")), ("cp_06_pot_on_table", ("pot_place",)), ("cp_07_surface_pick", ("fruit_surface_pick", "bowl_surface_pick")), ("cp_08_handoff_accepted", ("fruit_receive", "bowl_receive", "fruit_air_receive", "bowl_air_receive")), ("cp_09_sink_started", ("fruit_sink_pick",)), ("cp_10_sink_complete", ("fruit_sink_return",))):
            tick = completed(*ids)
            if tick is not None: rows.append({"label": label, "tick": tick})
        if result.get("success"): rows.append({"label": "cp_11_closeout", "tick": int(result.get("completion_tick", result.get("tick", 0)))})
        return rows


__all__ = ["LivingHouseScenarioAdapter"]
