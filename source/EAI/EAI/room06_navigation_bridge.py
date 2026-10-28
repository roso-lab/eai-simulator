"""Host bridge from typed room06 assignments to NavigationExecutor.

The bridge owns scheduling only. It never declares a skill or task successful;
the executor's live response is returned to the room06 driver for observer and
logical-session processing. Assignments are queued per robot so a decision may
cover several tasks without issuing concurrent requests to one controller.
"""
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import replace

from task.contracts import AlgorithmDecision
from algorithm.skills.navigation import NavigationResponse
from algorithm.skills.contracts import SkillRequest


class Room06NavigationBridgeError(RuntimeError):
    pass


class Room06NavigationBridge:
    def __init__(self, *, executor, task_by_id, advance, request_id_prefix="room06-nav"):
        if any(not callable(getattr(executor, method, None)) for method in
               ("dispatch", "prepare", "after_step", "needs_host_tick", "response", "adapter_for")) or not callable(advance):
            raise TypeError("NavigationExecutor and one shared host advance are required")
        self.executor = executor
        self.tasks = {str(k): deepcopy(v) for k, v in task_by_id.items()}
        self.advance = advance
        self.prefix = str(request_id_prefix)
        self.queues = defaultdict(deque)
        self.active = {}
        self.completed = {}
        self._sequence = 0
        self._decision_round = 0
        self._assigned_keys = set()
        self._operation_barriers = {}

    def wait_for_operation(self, robot, leg_id):
        """Keep an arrived participant from leaving for a queued task."""
        prior = self._operation_barriers.get(robot)
        if prior is not None and prior != leg_id:
            raise Room06NavigationBridgeError("robot already awaits another operation")
        self._operation_barriers[robot] = leg_id

    def finish_operation(self, leg_id):
        self._operation_barriers = {robot: leg for robot, leg in self._operation_barriers.items()
                                    if leg != leg_id}

    def accept_decision(self, decision: AlgorithmDecision):
        if not isinstance(decision, AlgorithmDecision):
            raise Room06NavigationBridgeError("typed AlgorithmDecision is required")
        pending = []
        pairs = set()
        for assignment in decision.assignments:
            task = self.tasks.get(assignment.task_id)
            if task is None:
                raise Room06NavigationBridgeError("decision references an unknown room06 task")
            if assignment.robot_id not in {r for r in self.tasks[assignment.task_id].get("allowed_robot_ids", ())}:
                # Empty allowed list means the public deployment pool decides;
                # capability filtering remains the tested system's choice.
                if self.tasks[assignment.task_id].get("allowed_robot_ids"):
                    raise Room06NavigationBridgeError("assignment robot is outside task binding")
            pair = (assignment.task_id, assignment.robot_id)
            if pair in pairs or pair in self._assigned_keys or any(pair[1] == robot and item[0] == pair[0]
                for robot, item in self.active.items()):
                raise Room06NavigationBridgeError("duplicate or already active assignment")
            pairs.add(pair)
            self.executor.adapter_for(assignment.robot_id)
            pending.append((assignment.robot_id, assignment.task_id, task))
        for robot, task_id, task in pending:
            self.queues[robot].append((task_id, task))
            self._assigned_keys.add((task_id, robot))
        self._decision_round += 1
        return {robot: len(queue) for robot, queue in sorted(self.queues.items())}

    def _start_next(self, robot, world_state_seq):
        if robot in self.active or robot in self._operation_barriers or not self.queues[robot]:
            return None
        task_id, task = self.queues[robot].popleft()
        self._sequence += 1
        parameters = {"intent": "start", "world_state_seq": world_state_seq}
        if task.get("route_graph_digest") is not None:
            parameters["route_graph_digest"] = task["route_graph_digest"]
        request = SkillRequest(
            request_id=f"{self.prefix}:{self._sequence}", task_id=f"{self.prefix}:{self._sequence}",
            skill_id="eai.navigation.node_route", robot_id=robot,
            target_ref=task["target_ref"],
            parameters=parameters,
        )
        self.executor.dispatch(request)
        self.active[robot] = (task_id, request)
        return request

    def step(self, *, world_state_seq):
        if type(world_state_seq) is not int or world_state_seq < 0:
            raise ValueError("world_state_seq must be a nonnegative integer")
        events = []
        for robot in sorted(set(self.queues) | set(self.active)):
            request = self._start_next(robot, world_state_seq)
            if request is None and robot not in self.active:
                continue
            if robot in self.active:
                task_id, active_request = self.active[robot]
                if not self.executor.needs_host_tick(active_request.task_id):
                    response = self.executor.response(active_request.task_id)
                    if response.state["status"] == "pending":
                        request = replace(active_request, parameters={**active_request.parameters,
                            "world_state_seq": world_state_seq,
                            "intent": "start" if request is not None else "continue"})
                        self.executor.prepare(request)
        # Exactly one shared simulator advance, even with multiple robots.
        self.advance()
        for robot, (task_id, active_request) in list(self.active.items()):
                response = (self.executor.after_step(active_request.task_id)
                    if self.executor.needs_host_tick(active_request.task_id)
                    else self.executor.response(active_request.task_id))
                if not isinstance(response, NavigationResponse):
                    raise Room06NavigationBridgeError("NavigationExecutor returned malformed response")
                status = response.state.get("status")
                if status in {"failed", "cancelled"} or (status == "completed" and response.state.get("stop_confirmed") is True):
                    del self.active[robot]
                    self.completed[(task_id, robot)] = response
                    events.append({"task_id": task_id, "robot_id": robot,
                                   "status": status, "response": deepcopy(response.state),
                                   "evidence": deepcopy(response.private_evidence)})
        return events


__all__ = ["Room06NavigationBridge", "Room06NavigationBridgeError"]
