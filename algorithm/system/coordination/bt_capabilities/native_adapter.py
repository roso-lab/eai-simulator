"""ROS-free client of an actual ros_bt_py runtime (never a local auction).

Start scripts/native_endpoint.py inside the isolated ROS1 environment. The
endpoint imports the original ParallelAuctionManager, Parallel and MemorySequence. This
client only translates public EAI records and returns native assignments.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
from typing import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment, ExecutionFeedback, FailureCode
from task.contracts import AlgorithmDecision, DependencyOutcome, EaiTaskPackage, ExecutionUpdate, RobotState


class NativeBTError(RuntimeError):
    pass


def encode_task(task: EaiTaskPackage, robots: Sequence[RobotState]) -> dict:
    """Translate a DAG without choosing assignments or inventing bid costs."""
    tasks = list(task.graph.tasks)
    if not tasks:
        raise ValueError("nonempty task graph required")
    if not task.graph.valid:
        raise ValueError("invalid task graph")
    remaining = {item.task_id: item for item in tasks}
    ordered = []
    previous = ()
    while remaining:
        ready = [item for item in remaining.values() if item.prerequisites == previous]
        if len(ready) != 1:
            break
        item = ready[0]
        ordered.append(item)
        del remaining[item.task_id]
        previous = (item.task_id,)
    graph_mode = "dag" if remaining else "linear"
    if graph_mode == "dag":
        ordered = tasks
    ids = [r.capability.robot_id for r in robots]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError("nonempty unique robot roster required")
    # Bind capability/active changes to the package first, so the runner and
    # native bid filtering see the same roster. Position is not an auction bid.
    if ({r.capability.robot_id: r.capability for r in robots}
            != {r.capability.robot_id: r.capability for r in task.robots}):
        raise ValueError("runtime robot roster differs from task package; bind_runtime first")
    records = []
    for item in ordered:
        config = item.metadata.get("bt_native", {})
        supplied = config.get("bids")
        if not isinstance(supplied, dict):
            raise ValueError(f"{item.task_id}: explicit bt_native.bids required (lower cost wins)")
        if set(supplied) - set(ids):
            raise ValueError(f"{item.task_id}: unknown supplied bid robot IDs")
        bids = {}
        for robot in robots:
            cap = robot.capability
            if not cap.active or not item.required_capabilities <= cap.capabilities:
                continue
            if cap.robot_id not in supplied:
                raise ValueError(f"{item.task_id}: missing explicit bid for {cap.robot_id}")
            value = supplied[cap.robot_id]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("bids must be finite nonnegative costs")
            bids[cap.robot_id] = float(value)
        records.append({"task_id": item.task_id, "description": item.description,
                        "required_capabilities": sorted(item.required_capabilities),
                        "target_ref": item.target_ref, "bids": bids,
                        "prerequisites": list(item.prerequisites)})
    return {"task_id": task.task_id, "scene_id": task.scene_id, "tasks": records,
            "robot_ids": ids, "graph_mode": graph_mode}


class NativeBTCapabilitiesAdapter:
    """Native endpoint client; one active mission per adapter instance.

    Endpoint configuration is explicit or HETEROCOORD_BT_NATIVE_ENDPOINT. Source
    resolution is explicit, HETEROCOORD_BT_SOURCE_ROOT, then the default cache.
    The parent's shared source-integrity gate should additionally wrap this class.
    """
    def __init__(self, source_root=None, *, endpoint=None, timeout_s=30.0):
        self.source_root = Path(source_root or os.environ.get(
            "HETEROCOORD_BT_SOURCE_ROOT",
            "/home/airs/.cache/heterocoord/upstream/bt-capabilities")).expanduser().resolve()
        if not (self.source_root / "ros_bt_py/src/ros_bt_py/assignment_manager/parallel_auction_manager.py").is_file():
            raise NativeBTError(f"upstream source missing: {self.source_root}")
        self.endpoint = (endpoint or os.environ.get("HETEROCOORD_BT_NATIVE_ENDPOINT", "")).rstrip("/")
        if not self.endpoint.startswith("http://"):
            raise NativeBTError("explicit http:// ROS1 endpoint required; no fallback")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive and finite")
        self.timeout_s = timeout_s
        self.mission_id = None
        self._payload = None
        self.last_native_state = None

    def _request(self, route, payload):
        request = Request(self.endpoint + route,
                          json.dumps(payload, allow_nan=False).encode(),
                          {"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                result = json.load(response)
        except HTTPError as exc:
            # The endpoint returns bounded diagnostic text, never environment variables.
            raise NativeBTError(f"native endpoint {route}: {exc.read(4096).decode()}") from exc
        except (URLError, TimeoutError) as exc:
            raise NativeBTError(f"native endpoint unavailable at {self.endpoint}") from exc
        if not result.get("ok"):
            raise NativeBTError(result.get("error", "invalid native response"))
        return result

    def _decision(self, result):
        self.last_native_state = result  # preserve the complete native output on scope errors
        if (not isinstance(self.mission_id, str) or not self.mission_id.strip()
                or result.get("mission_id") != self.mission_id):
            raise NativeBTError("native endpoint mission_id differs from active mission")
        if self._payload is None:
            raise NativeBTError("no native task payload bound")
        contract_hash = hashlib.sha256(json.dumps(
            self._payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if result.get("task_contract_sha256") != contract_hash:
            raise NativeBTError("native endpoint task_contract_sha256 differs from requested payload")
        if result.get("native_algorithm") != "ros_bt_py.assignment_manager.parallel_auction_manager.ParallelAuctionManager":
            raise NativeBTError("endpoint did not identify the required native algorithm")
        required_hashes = {"ros_bt_py/src/ros_bt_py/assignment_manager/parallel_auction_manager.py",
                           "ros_bt_py/src/ros_bt_py/nodes/sequence.py"}
        graph_mode = self._payload["graph_mode"]
        if result.get("graph_mode") != graph_mode:
            raise NativeBTError("native endpoint graph_mode differs from requested graph")
        native_tree = ("ros_bt_py.nodes.parallel.Parallel" if graph_mode == "dag"
                       else "ros_bt_py.nodes.sequence.MemorySequence")
        if result.get("native_tree") != native_tree:
            raise NativeBTError("native endpoint native_tree differs from requested graph_mode")
        if graph_mode == "dag":
            required_hashes.add("ros_bt_py/src/ros_bt_py/nodes/parallel.py")
        received_hashes = set(result.get("source_sha256", {}))
        allowed_hashes = required_hashes | {"ros_bt_py/src/ros_bt_py/nodes/parallel.py"}
        if not required_hashes <= received_hashes or not received_hashes <= allowed_hashes:
            raise NativeBTError("native endpoint source receipt is incomplete")
        for relative, expected in result.get("source_sha256", {}).items():
            path = (self.source_root / relative).resolve()
            if not path.is_relative_to(self.source_root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise NativeBTError("native endpoint source differs from configured source_root")
        assigned_ids = [item["task_id"] for item in result["assignments"]]
        if len(assigned_ids) != len(set(assigned_ids)):
            raise NativeBTError("native coalition output cannot map to TaskRunner single-robot assignments; raw output retained")
        robot_ids = [item["robot_id"] for item in result["assignments"]]
        if len(robot_ids) != len(set(robot_ids)):
            raise NativeBTError("native assignments occupy the same robot slot more than once")
        task_by_id = {item["task_id"]: item for item in self._payload["tasks"]}
        for item in result["assignments"]:
            if item["task_id"] not in task_by_id:
                raise NativeBTError("native assignment references an unknown task")
            if item["robot_id"] not in self._payload["robot_ids"]:
                raise NativeBTError("native assignment references an unknown robot")
            if item["robot_id"] not in task_by_id[item["task_id"]]["bids"]:
                raise NativeBTError("native assignment violates declared bid eligibility")
        condition = AlgorithmCondition(
            algorithm_id="BT-capabilities-native", source_reference=result["source_commit"],
            implementation="original_ros_bt_py_via_ros1_endpoint",
            execution_scope=("native_auction_and_linear_memory_sequence" if graph_mode == "linear"
                             else "native_auction_and_parallel_memory_sequences"),
            planner=result["native_algorithm"],
            supported_primitives=("native_auction", "linear_mission", "dag_mission", "execution_feedback"),
            unsupported_dimensions=("automatic_reauction_after_failure", "physical_success_without_evaluator"))
        assignments = tuple(Assignment(item["task_id"], item["robot_id"],
                                       "BT-capabilities-native", result["mission_id"])
                            for item in result["assignments"])
        return AlgorithmDecision(condition, assignments, tuple(result["unassigned_tasks"]), {
            "contract_evidence": "validated_" + graph_mode + "_and_explicit_costs",
            "native_evidence": result,
            "simulation_evidence": "not_evaluated",
            "physical_evidence": "not_evaluated",
            "source_root": str(self.source_root),
        })

    def __call__(self, task: EaiTaskPackage, robots: Sequence[RobotState]) -> AlgorithmDecision:
        payload = encode_task(task, robots)
        if self.mission_id is not None:
            raise NativeBTError("mission already started; use replan() or a new adapter")
        result = self._request("/plan", payload)
        self.mission_id, self._payload = result.get("mission_id"), payload
        return self._decision(result)

    def replan(self, task, robots, state=None):
        if encode_task(task, robots) != self._payload:
            raise NativeBTError("changed mission/roster requires a new native runtime mission")
        return self._decision(self._request("/state", {"mission_id": self.mission_id}))

    def feedback(self, feedback, *, succeeded: bool):
        """Forward an executor-owned terminal result, without interpreting outcome strings."""
        if type(succeeded) is not bool:
            raise TypeError("explicit boolean succeeded required")
        if self.mission_id is None:
            raise NativeBTError("no native mission started")
        result = self._request("/feedback", {"mission_id": self.mission_id,
            "task_id": feedback.task_id, "robot_id": feedback.robot_id,
            "succeeded": succeeded, "outcome": feedback.outcome,
            "failure_code": feedback.failure_code.value})
        self._decision(result)
        return result

    def feedback_batch(self, feedback, *, succeeded):
        """Submit terminal receipts atomically; the endpoint validates before ticking."""
        if self.mission_id is None:
            raise NativeBTError("no native mission started")
        feedback, succeeded = tuple(feedback), tuple(succeeded)
        if not feedback or len(feedback) != len(succeeded):
            raise ValueError("nonempty feedback batch and matching succeeded flags required")
        if any(type(value) is not bool for value in succeeded):
            raise TypeError("explicit boolean succeeded required")
        ids = [item.task_id for item in feedback]
        if len(ids) != len(set(ids)):
            raise NativeBTError("duplicate feedback in batch")
        records = [{"task_id": item.task_id, "robot_id": item.robot_id,
                    "succeeded": value, "outcome": item.outcome,
                    "failure_code": item.failure_code.value}
                   for item, value in zip(feedback, succeeded)]
        result = self._request("/feedback_batch", {"mission_id": self.mission_id, "feedback": records})
        self._decision(result)
        return result

    def close(self):
        if self.mission_id:
            self._request("/close", {"mission_id": self.mission_id})
            self.mission_id = None


class NativeBTFeedbackExecutor:
    """Wrap an existing EAI executor; retain its feedback and evaluator boundary."""
    def __init__(self, adapter, executor, *, success_predicate=None):
        self.adapter, self.executor, self.success_predicate = adapter, executor, success_predicate

    def bind_run(self, request):
        bind = getattr(self.executor, "bind_run", None)
        if callable(bind):
            bind(request)

    def _update(self, result):
        if isinstance(result, ExecutionUpdate):
            update = result
        elif isinstance(result, ExecutionFeedback) and self.success_predicate is not None:
            succeeded = self.success_predicate(result)
            if type(succeeded) is not bool:
                raise TypeError("explicit boolean succeeded required")
            update = ExecutionUpdate(result, DependencyOutcome.COMPLETED if succeeded
                                     else DependencyOutcome.TERMINAL_FAILURE)
        else:
            raise TypeError("executor must return ExecutionUpdate or feedback with an explicit success_predicate")
        if (update.dependency_outcome is DependencyOutcome.COMPLETED) != (update.feedback.failure_code is FailureCode.NONE):
            raise NativeBTError("dependency outcome contradicts execution failure code")
        return update

    def execute(self, assignment, task, *, scene):
        result = self.executor.execute(assignment, task, scene=scene)
        update = self._update(result)
        feedback = update.feedback
        if (feedback.task_id, feedback.robot_id) != (assignment.task_id, assignment.robot_id):
            raise NativeBTError("executor feedback identity mismatch")
        self.adapter.feedback(feedback, succeeded=update.dependency_outcome is DependencyOutcome.COMPLETED)
        return result

    def execute_batch(self, assignments, tasks, *, scene):
        assignments = tuple(assignments)
        if not assignments:
            return ()
        expected = [(item.task_id, item.robot_id) for item in assignments]
        if len({item.task_id for item in assignments}) != len(assignments):
            raise NativeBTError("duplicate task in dispatch batch")
        if len({item.robot_id for item in assignments}) != len(assignments):
            raise NativeBTError("dispatch batch occupies the same robot slot more than once")
        batch = getattr(self.executor, "execute_batch", None)
        if callable(batch):
            results = tuple(batch(assignments, tasks, scene=scene))
        else:
            results = tuple(self.executor.execute(item, tasks[item.task_id], scene=scene)
                            for item in assignments)
        updates = tuple(self._update(item) for item in results)
        identities = [(item.feedback.task_id, item.feedback.robot_id) for item in updates]
        if len(identities) != len(set(identities)):
            raise NativeBTError("duplicate executor feedback in batch")
        if set(identities) != set(expected):
            raise NativeBTError("executor feedback identity mismatch")
        by_identity = dict(zip(identities, updates))
        ordered = tuple(by_identity[key] for key in expected)
        self.adapter.feedback_batch(tuple(item.feedback for item in ordered),
                                    succeeded=tuple(item.dependency_outcome is DependencyOutcome.COMPLETED
                                                    for item in ordered))
        return ordered
