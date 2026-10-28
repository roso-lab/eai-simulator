"""Join measured navigation completion to logical skill sessions."""
from copy import deepcopy
from dataclasses import replace

from task.logical_objects import LogicalObjectRuntime
from .room06_navigation_bridge import Room06NavigationBridge


class Room06ExecutionCoordinatorError(RuntimeError):
    pass


class Room06ExecutionCoordinator:
    """Translate one completed navigation leg into one host skill session.

    ``leg_bindings`` is host-owned case data. It may describe pick, receive,
    join, leave, or deliver, but never comes from the tested system. A
    navigation response alone produces no benchmark milestone.
    """
    def __init__(self, *, navigation: Room06NavigationBridge, logical_runtime: LogicalObjectRuntime,
                 leg_bindings):
        if not isinstance(navigation, Room06NavigationBridge):
            raise TypeError("room06 navigation bridge is required")
        if not isinstance(logical_runtime, LogicalObjectRuntime):
            raise TypeError("logical object runtime is required")
        if not isinstance(leg_bindings, dict):
            raise TypeError("host leg bindings must be a mapping")
        self.navigation = navigation
        self.runtime = logical_runtime
        self.bindings = deepcopy(leg_bindings)
        self.arrived = {}
        self.failed = set()
        self.executed = set()
        self.completed = set()
        self.pending = set()

    def accept_decision(self, decision):
        # The public decision names transport tasks; the host expands one
        # assignment into an explicitly bound operation leg before accepting.
        expanded = []
        grouped = {}
        for assignment in decision.assignments:
            if assignment.task_id.startswith("transport:"):
                entity = assignment.task_id.removeprefix("transport:")
                if any(f"{phase}:{entity}" in self.failed for phase in ("pickup", "handoff", "delivery")):
                    raise Room06ExecutionCoordinatorError("failed transport cannot skip to a later leg")
                phases = [phase for phase in ("pickup", "handoff", "delivery")
                          if f"{phase}:{entity}" in self.bindings
                          and f"{phase}:{entity}" not in self.completed]
                if not phases:
                    raise Room06ExecutionCoordinatorError("transport task has no remaining host leg")
                leg_id = f"{phases[0]}:{entity}"
            else:
                leg_id = assignment.task_id
            binding = self.bindings.get(leg_id)
            if binding is None:
                raise Room06ExecutionCoordinatorError("decision has no host leg binding")
            phase = leg_id.split(":", 1)[0] if ":" in leg_id else ""
            entity = leg_id.split(":", 1)[1] if ":" in leg_id else ""
            prerequisites = {"handoff": f"pickup:{entity}", "delivery":
                             (f"handoff:{entity}" if f"handoff:{entity}" in self.bindings
                              else f"pickup:{entity}")}
            required = prerequisites.get(phase)
            if required is not None and required not in self.completed:
                raise Room06ExecutionCoordinatorError("host leg prerequisite is incomplete")
            if leg_id in self.pending or leg_id in self.executed or leg_id in self.failed:
                raise Room06ExecutionCoordinatorError("host leg is already submitted or terminal")
            grouped.setdefault(leg_id, []).append(assignment.robot_id)
            expanded.append(replace(assignment, task_id=leg_id))
        bindings = deepcopy(self.bindings)
        for leg_id, robots in grouped.items():
            binding = bindings[leg_id]
            if "robot_ids" in binding and set(binding["robot_ids"]) != set(robots):
                raise Room06ExecutionCoordinatorError("host members differ from actual assignment")
            binding["robot_ids"] = list(dict.fromkeys(robots))
        result = self.navigation.accept_decision(replace(decision, assignments=tuple(expanded)))
        self.bindings = bindings
        self.pending.update(grouped)
        return result

    def step(self, *, world_state_seq):
        records = []
        for event in self.navigation.step(world_state_seq=world_state_seq):
            binding = self.bindings.get(event["task_id"])
            if not isinstance(binding, dict) or set(binding) != {
                "operation", "object_id", "robot_ids", "semantic_point", "event_type"
            }:
                raise Room06ExecutionCoordinatorError("completed navigation leg lacks host binding")
            if event["status"] != "completed":
                self.failed.add(event["task_id"])
                self.pending.discard(event["task_id"])
                records.append({"event": event, "skill_event": None})
                continue
            robot_ids = list(binding["robot_ids"])
            if event["robot_id"] not in robot_ids:
                raise Room06ExecutionCoordinatorError("navigation leg holder identity differs from host binding")
            leg = event["task_id"]
            if leg in self.failed or leg in self.executed:
                continue
            self.navigation.wait_for_operation(event["robot_id"], leg)
            self.arrived.setdefault(leg, set()).add(event["robot_id"])
            if leg in self.failed or leg in self.executed or self.arrived[leg] != set(robot_ids):
                continue
            self.executed.add(leg)  # Never retry an uncertain host session.
            result = self.runtime.execute(
                request_id=f"logical:{leg}",
                operation=binding["operation"], object_id=binding["object_id"],
                robot_ids=robot_ids, semantic_point=binding["semantic_point"],
            )
            self.pending.discard(leg)
            if result["status"] == "completed":
                self.completed.add(leg)
                self.navigation.finish_operation(leg)
            else:
                self.failed.add(leg)
            skill_event = None if binding["event_type"] is None or result["status"] != "completed" else {
                "schema": "heterocoord.observed-skill-event.v1",
                "session_id": result["session_id"], "event_type": binding["event_type"],
                "status": result["status"], "tick": result["tick"],
                "robot_id": event["robot_id"], "object_id": result["object_id"],
                "target_ref": result["semantic_point"],
                "evidence_digest": result["evidence_digest"],
            }
            records.append({"event": event, "skill_event": skill_event})
        return records


__all__ = ["Room06ExecutionCoordinator", "Room06ExecutionCoordinatorError"]
