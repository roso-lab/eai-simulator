"""I/O boundary to the official TeamWeaver HRCS solver and semantic planner.

This is NOT the rewritten ``eai_adapter`` implementation. The supported scope
includes supplied DAG allocation/feedback and single-snapshot native semantic
planning. The latter uses two audited syntax repairs; no solver or utility is
replaced. Prompt/output and environment adapters are explicitly recorded.
"""
from __future__ import annotations

import contextlib
import difflib
import hashlib
import importlib
import importlib.util
import io
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState

EXPECTED_COMMIT = "edf4619e4730860b96eebaac39620df4bbdc486a"
DEFAULT_SOURCE_ROOT = Path("/home/airs/.cache/heterocoord/upstream/teamweaver")
DEFAULT_PYTHON = Path("/home/airs/.cache/heterocoord/envs/teamweaver-native/bin/python")
SEMANTIC_PYTHON = Path("/home/airs/.cache/heterocoord/envs/teamweaver-semantic/bin/python")
PARTNR_COMMIT = "ddfff19f4b6c098a31edea4d19e7b75db72433c2"
HABITAT_LAB_COMMIT = "094d6be2f9d057e4781a68ae792132895fd4d3d0"
SEMANTIC_MODULE = "habitat_llm.planner.llm_planner_comparison.llm_planner_miqp"
ENTRYPOINT = "class_def.RTA_task.RTA.solve_miqp_phase_aware"
SCOPE = "supplied_decomposition"
# These are native task types with an explicit target/state mapping here.
TARGET_KEYS = {
    "Navigate": "p_goal", "Pick": "target_object_position",
    "Place": "target_receptacle_position", "Open": "target_furniture_position",
    "Close": "target_furniture_position",
}


class NativeScopeError(ValueError):
    """The native input/output cannot be represented by this EAI contract."""

    def __init__(self, message: str, *, native_result: Mapping | None = None):
        super().__init__(message)
        self.native_result = dict(native_result or {})


class NativeSolverError(RuntimeError):
    """The real native solver failed; no alternate allocator is attempted."""

    def __init__(self, message: str, *, native_result: Mapping | None = None):
        super().__init__(message)
        self.native_result = dict(native_result or {})


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise NativeScopeError(f"{label} must be finite numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeScopeError(f"{label} must be finite numeric") from exc
    if not math.isfinite(number):
        raise NativeScopeError(f"{label} must be finite numeric")
    return number


def _position(value: Any, label: str, dimensions: tuple[int, ...]) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) not in dimensions:
        raise NativeScopeError(f"{label} requires coordinates of length {dimensions}")
    return [_finite(v, label) for v in value]


class NativeTeamWeaverAdapter:
    """Callable native decision provider; no implicit history or fake LLM.

    ``source_root`` falls back to HETEROCOORD_TEAMWEAVER_SOURCE_ROOT, then the
    documented cache. ``python_executable`` falls back to
    HETEROCOORD_TEAMWEAVER_PYTHON, the isolated cache interpreter if present,
    then sys.executable. Native imports and Gurobi execute in a fresh process.

    Task metadata must declare ``native_task_type`` and ``target_position``
    (or ``target_xy``). Dependencies use evaluator-supplied
    package.metadata['completed_task_ids']; this is not native decomposition
    or native phase management. Position is projected to XY; robot heading is
    metadata['yaw_rad'] (required). Manipulation additionally requires explicit
    package.metadata['teamweaver_global_vars'] state. Solver defaults survive.
    """

    def __init__(self, source_root: str | Path | None = None, *,
                 python_executable: str | Path | None = None,
                 semantic_python_executable: str | Path | None = None,
                 partnr_root: str | Path | None = None,
                 habitat_lab_root: str | Path | None = None,
                 live_llm_probe: bool = False,
                 output_seam: bool = False,
                 source_compatibility: bool = True,
                 scope: str = SCOPE, timeout_s: float = 60):
        self.source_root = Path(source_root or os.environ.get(
            "HETEROCOORD_TEAMWEAVER_SOURCE_ROOT", DEFAULT_SOURCE_ROOT)).expanduser().resolve()
        self.python_executable = str(python_executable or os.environ.get(
            "HETEROCOORD_TEAMWEAVER_PYTHON") or (
                DEFAULT_PYTHON if DEFAULT_PYTHON.exists() else sys.executable))
        self.semantic_python_executable = str(semantic_python_executable or
            (python_executable if scope == "full_semantic" else None) or os.environ.get(
                "HETEROCOORD_TEAMWEAVER_SEMANTIC_PYTHON") or SEMANTIC_PYTHON)
        self.partnr_root = Path(partnr_root or os.environ.get("HETEROCOORD_TEAMWEAVER_PARTNR_ROOT",
            "/home/airs/.cache/heterocoord/upstream/teamweaver-partnr")).expanduser().resolve()
        self.habitat_lab_root = Path(habitat_lab_root or os.environ.get("HETEROCOORD_TEAMWEAVER_HABITAT_LAB_ROOT",
            "/home/airs/.cache/heterocoord/upstream/teamweaver-habitat-lab")).expanduser().resolve()
        if scope not in (SCOPE, "full_semantic"):
            raise ValueError("scope must be supplied_decomposition or full_semantic")
        self.scope = scope
        if not isinstance(live_llm_probe, bool):
            raise ValueError("live_llm_probe must be boolean")
        self.live_llm_probe = live_llm_probe
        if not isinstance(output_seam, bool):
            raise ValueError("output_seam must be boolean")
        self.output_seam = output_seam
        if not isinstance(source_compatibility, bool):
            raise ValueError("source_compatibility must be boolean")
        self.source_compatibility = source_compatibility
        self.timeout_s = _finite(timeout_s, "timeout_s")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.last_native_result: dict[str, Any] | None = None

    def _invoke(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        # -I prevents PYTHONPATH/import pollution from concurrent adapters.
        # No bytecode, log files, or namespace symlinks are written upstream.
        with tempfile.TemporaryDirectory(prefix="teamweaver-native-") as work:
            try:
                interpreter = (self.semantic_python_executable if payload["operation"] != "solve"
                               else self.python_executable)
                process = subprocess.run(
                    [interpreter, "-I", "-B", str(Path(__file__).resolve()), "--worker"],
                    input=json.dumps({"source_root": str(self.source_root),
                                      "partnr_root": str(self.partnr_root),
                                      "source_compatibility": self.source_compatibility, **payload}, allow_nan=False),
                    text=True, capture_output=True, cwd=work, timeout=self.timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                raise NativeSolverError("native subprocess timed out; no fallback") from exc
            except OSError as exc:
                raise NativeSolverError("native interpreter unavailable") from exc
        if process.returncode:
            # Do not propagate arbitrary dependency logs/environment secrets.
            raise NativeSolverError(f"native subprocess exited {process.returncode}; no fallback")
        try:
            result = json.loads(process.stdout)
        except ValueError as exc:
            raise NativeSolverError("native subprocess returned invalid JSON") from exc
        self.last_native_result = result
        if result.get("error"):
            raise NativeSolverError(
                f"native execution failed: {result['error']}"
                + (f" (missing {result['missing_module']})" if result.get("missing_module") else ""),
                native_result=result,
            )
        return result

    def probe_full_semantic(self) -> dict[str, Any]:
        """Actually import the original planner, with pinned real PARTNR files."""
        return self._semantic_probe({"operation": "probe_full_semantic"})

    def run_full_semantic(self, task, robots):
        """Run actual native decomposition/MIQP/action planning; no execution claim."""
        payload = _map_semantic_input(task, tuple(robots))
        payload["output_seam"] = self.output_seam
        return self._semantic_probe({"operation": "run_full_semantic", "semantic_input": payload})

    def prepare_full_semantic(self, task: EaiTaskPackage,
                              robots: Sequence[RobotState], *,
                              parser_probe_response: str | None = None) -> dict[str, Any]:
        """Execute original world/agent/tool-description mapping; never a decision.

        This is a preparation receipt, not a fabricated EnvironmentInterface,
        decomposition, motor skill execution, or successful semantic plan.
        """
        payload = _map_semantic_input(task, tuple(robots))
        payload["live_llm_probe"] = self.live_llm_probe
        payload["output_seam"] = self.output_seam
        if parser_probe_response is not None:
            if not isinstance(parser_probe_response, str):
                raise NativeScopeError("parser_probe_response must be text")
            payload["parser_probe_response"] = parser_probe_response
        return self._semantic_probe({"operation": "prepare_full_semantic", "semantic_input": payload})

    def _semantic_probe(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        from algorithm.system.coordination.native_runtime import verify_source
        before = verify_source(self.source_root, EXPECTED_COMMIT)
        dependencies = {"partnr": verify_source(self.partnr_root, PARTNR_COMMIT),
                        "habitat_lab": verify_source(self.habitat_lab_root, HABITAT_LAB_COMMIT)}
        try:
            result = self._invoke(payload)
        finally:
            after = verify_source(self.source_root, EXPECTED_COMMIT)
            if before != after:
                raise NativeSolverError("upstream source changed during probe")
            for receipt in dependencies.values():
                if verify_source(receipt.root, receipt.commit) != receipt:
                    raise NativeSolverError("semantic dependency source changed during probe")
        result.update(source=before.to_dict(), source_unchanged=True,
                      dependency_sources={k: v.to_dict() for k, v in dependencies.items()},
                      interpreter=self.semantic_python_executable)
        self.last_native_result = result
        return result

    def __call__(self, task: EaiTaskPackage, robots: Sequence[RobotState]) -> AlgorithmDecision:
        from algorithm.common_coordination.contract import AlgorithmCondition, Assignment
        from algorithm.system.coordination.native_runtime import verify_source
        from task.contracts import AlgorithmDecision, EaiTaskPackage

        self.last_native_result = None
        if self.scope != SCOPE and self.source_compatibility:
            return self._semantic_decision(task, robots)
        if self.scope != SCOPE:
            result = self.prepare_full_semantic(task, robots)
            blocker = result.get("planner_import", {})
            raise NativeScopeError(
                "full_semantic is blocked: " + blocker.get("error_type", "semantic execution unverified")
                + (f" at {blocker['file']}:{blocker['line']}" if blocker.get("file") else "")
                + "; native preparation/import receipt preserved; no substituted plan",
                native_result=result,
            )
        if not isinstance(task, EaiTaskPackage) or not task.graph.valid:
            raise NativeScopeError("a valid EaiTaskPackage is required")
        robots = tuple(robots)
        robot_ids = [r.capability.robot_id for r in robots]
        if len(set(robot_ids)) != len(robot_ids) or set(robot_ids) != {
            r.capability.robot_id for r in task.robots
        }:
            raise NativeScopeError("runtime/package robot roster mismatch")
        completed = task.metadata.get("completed_task_ids", ())
        if not isinstance(completed, (list, tuple)) or any(not isinstance(v, str) for v in completed):
            raise NativeScopeError("completed_task_ids must be an explicit sequence of task ids")
        completed = set(completed)
        known = {t.task_id for t in task.graph.tasks}
        if not completed <= known:
            raise NativeScopeError("completed_task_ids contains unknown tasks")
        for item in task.graph.tasks:
            if item.task_id in completed and not set(item.prerequisites) <= completed:
                raise NativeScopeError("completed history is not dependency-closed")
        active = [r for r in robots if r.capability.active]
        ready = [t for t in task.graph.tasks if t.task_id not in completed
                 and set(t.prerequisites) <= completed]
        before = verify_source(self.source_root, EXPECTED_COMMIT)
        condition = AlgorithmCondition(
            algorithm_id="TeamWeaver-native-HRCS", source_reference=f"{self.source_root}@{EXPECTED_COMMIT}",
            implementation="unmodified-upstream-solver-with-io-mapping",
            execution_scope=SCOPE, planner=ENTRYPOINT, model_or_api="CVXPY/GUROBI",
            supported_primitives=tuple(TARGET_KEYS),
            unsupported_dimensions=("semantic_decomposition", "native_phase_management",
                                    "coalition_dispatch", "physical_execution"),
            notes="Externally supplied frontier only; original utilities, objective and constraints unchanged.",
        )
        provenance: dict[str, Any] = {
            "source": before.to_dict(), "execution_scope": SCOPE,
            "full_semantic_status": "blocked", "decomposition_source": "supplied_EaiTaskPackage",
            "frontier_source": "supplied_graph_and_completed_task_ids",
            "completed_task_ids": sorted(completed), "ready_task_ids": [t.task_id for t in ready],
            "robot_ids": [r.capability.robot_id for r in active],
            "solver_entrypoint": ENTRYPOINT, "solver_invoked": False,
            "physical_evidence": "N/A", "prompts_invoked": False,
        }
        if not active or not ready:
            return AlgorithmDecision(condition, unassigned_tasks=tuple(
                t.task_id for t in task.graph.tasks if t.task_id not in completed), provenance=provenance)
        payload = self._map_input(task, active, ready)
        try:
            native = self._invoke({"operation": "solve", **payload})
        finally:
            after = verify_source(self.source_root, EXPECTED_COMMIT)
            if before != after:
                raise NativeSolverError("upstream source changed during solve")
        provenance.update(solver_invoked=True, native_input=payload, native_result=native)
        if native["status"] != "Optimal" or native.get("native_error_count", 0):
            raise NativeSolverError(f"native MIQP did not produce an acceptable solution: {native['status']}",
                                    native_result=native)
        alpha = native["alpha"]
        if len(alpha) != len(active) or any(len(row) != len(ready) for row in alpha):
            raise NativeSolverError("native assignment matrix shape mismatch", native_result=native)
        if any(not math.isfinite(v) or abs(v - round(v)) > 1e-5 or round(v) not in (0, 1)
               for row in alpha for v in row):
            raise NativeSolverError("native assignment matrix is not binary", native_result=native)
        if any(sum(round(v) for v in row) != 1 for row in alpha):
            raise NativeSolverError("native per-robot assignment invariant failed", native_result=native)
        assignments = []
        for j, item in enumerate(ready):
            owners = [i for i, row in enumerate(alpha) if round(row[j]) == 1]
            if len(owners) > 1:
                raise NativeScopeError(
                    f"native coalition for {item.task_id}: TaskRunner rejects multi-robot same-task assignments; "
                    "complete alpha preserved in native_result, no owner selected", native_result=native)
            for i in owners:
                robot = active[i]
                if not item.required_capabilities <= robot.capability.capabilities:
                    raise NativeScopeError("native assignment cannot meet EAI individual capabilities",
                                           native_result=native)
                assignments.append(Assignment(item.task_id, robot.capability.robot_id,
                                              "TeamWeaver-original-HRCS-MIQP", EXPECTED_COMMIT))
        assigned = {a.task_id for a in assignments}
        return AlgorithmDecision(condition, tuple(assignments), tuple(
            t.task_id for t in task.graph.tasks if t.task_id not in assigned | completed), provenance)

    def _semantic_decision(self, task, robots):
        from algorithm.common_coordination.contract import AlgorithmCondition, Assignment
        from task.contracts import AlgorithmDecision
        if task.metadata.get("completed_task_ids"):
            raise NativeScopeError("semantic snapshot must start fresh; native phase resume requires a persistent session")
        result = self.run_full_semantic(task, robots)
        planner = result.get("native_planner", {})
        if not planner.get("planning_success") or result.get("native_error_count"):
            raise NativeSolverError("native semantic planning failed; full receipt retained", native_result=result)
        output = planner.get("output_validation", {})
        if output.get("status") != "boundary_valid_only":
            raise NativeScopeError("native semantic output is not unambiguously dispatchable", native_result=result)
        assignments = tuple(Assignment(c["task_id"], c["robot_id"], "TeamWeaver-native-semantic", EXPECTED_COMMIT)
                            for c in output["dispatch_candidates"])
        assigned = {a.task_id for a in assignments}
        condition = AlgorithmCondition(
            algorithm_id="TeamWeaver-native-semantic", source_reference=f"{self.source_root}@{EXPECTED_COMMIT}",
            implementation="original_planner_with_audited_syntax_and_EAI_IO",
            execution_scope="full_semantic_snapshot", planner=SEMANTIC_MODULE + ".LLMPlanner.replan",
            model_or_api=os.environ.get("DONGLI_MODEL", "unconfigured"),
            prompt_revision="eai_output_seam_v2" if self.output_seam else "original_prompt",
            supported_primitives=("Navigate", "Explore", "Pick", "Open", "Close"),
            unsupported_dimensions=("persistent_semantic_replanning", "coalition_dispatch", "Habitat_motor_execution"),
            notes="Actual native decomposition/MIQP/action generation on one explicit world snapshot; EAI owns execution.",
        )
        return AlgorithmDecision(condition, assignments,
            tuple(n.task_id for n in task.graph.tasks if n.task_id not in assigned),
            {"native_result": result, "source_compatibility": result["source_compatibility"],
             "prompts_invoked": True, "solver_invoked": True, "physical_evidence": "N/A"})

    def replan(self, task, robots, state):
        """Translate executor-owned completion; re-solve using current observations."""
        if self.scope != SCOPE:
            raise NativeScopeError("persistent semantic replanning is not implemented; do not reset native phases silently")
        from dataclasses import replace
        from algorithm.common_coordination.contract import FailureCode
        ids = {n.task_id for n in task.graph.tasks}
        completed, pending = set(state.completed_task_ids), set(state.pending_task_ids)
        if state.failed_task_ids or state.blocked_task_ids:
            raise NativeScopeError("terminal failures require explicit native recovery semantics")
        if (completed & pending or completed | pending != ids
                or len(completed) != len(state.completed_task_ids)
                or len(pending) != len(state.pending_task_ids)):
            raise NativeScopeError("replan state must partition the task roster")
        robot_ids = {r.capability.robot_id for r in robots}
        latest = {a.task_id: a.robot_id for a in state.last_decision.assignments} if state.last_decision else {}
        seen = set()
        for feedback in state.feedback:
            if (feedback.task_id not in completed or feedback.robot_id not in robot_ids
                    or feedback.task_id in seen or feedback.failure_code is not FailureCode.NONE):
                raise NativeScopeError("completion contradicts feedback identity or failure status")
            if feedback.task_id in latest and latest[feedback.task_id] != feedback.robot_id:
                raise NativeScopeError("feedback robot differs from dispatched owner")
            seen.add(feedback.task_id)
        initial = set(task.metadata.get("completed_task_ids", ()))
        if completed - initial - seen:
            raise NativeScopeError("completion requires executor feedback")
        metadata = {**task.metadata, "completed_task_ids": sorted(completed)}
        # Runtime object state is host-owned, never inferred from action text.
        live = metadata.get("runtime_observation", {}).get("objects", {})
        if "teamweaver_global_vars" in live:
            metadata["teamweaver_global_vars"] = live["teamweaver_global_vars"]
        return self(replace(task, robots=tuple(robots), metadata=metadata), robots)

    @staticmethod
    def _map_input(package, robots, ready) -> dict[str, Any]:
        # A/Hs/ws encode the contract's boolean capability vocabulary directly.
        # No cost matrix, allocation heuristic, or solver implementation lives here.
        capabilities = sorted(set().union(*(r.capability.capabilities for r in robots),
                                          *(t.required_capabilities for t in ready)))
        if not capabilities or any(not t.required_capabilities for t in ready):
            raise NativeScopeError("native allocation requires explicit nonempty task capabilities")
        global_vars = package.metadata.get("teamweaver_global_vars", {})
        if not isinstance(global_vars, Mapping):
            raise NativeScopeError("teamweaver_global_vars must be a mapping")
        global_vars = dict(global_vars)
        # Only mapped state and scalar utility parameters; no hidden robot index guesses.
        allowed = {"is_holding", "holding_robot_id", "furniture_open_state", "theta_goal",
                   "dist_thresh", "orientation_weight", "pick_dist_thresh", "place_dist_thresh",
                   "operation_dist_thresh"}
        if set(global_vars) - allowed:
            raise NativeScopeError("unmapped teamweaver_global_vars keys")
        for key in global_vars:
            if key not in {"is_holding", "holding_robot_id", "furniture_open_state"}:
                global_vars[key] = _finite(global_vars[key], key)
                if key.endswith("thresh") and global_vars[key] <= 0:
                    raise NativeScopeError(f"{key} must be positive")
                if key == "orientation_weight" and global_vars[key] < 0:
                    raise NativeScopeError("orientation_weight must be nonnegative")
        for key in ("is_holding", "furniture_open_state"):
            if key in global_vars and not isinstance(global_vars[key], bool):
                raise NativeScopeError(f"{key} must be boolean")
        ids = [r.capability.robot_id for r in robots]
        if "holding_robot_id" in global_vars:
            owner = global_vars["holding_robot_id"]
            if owner is not None and owner not in ids:
                raise NativeScopeError("holding_robot_id must identify an active robot or be null")
            global_vars["holding_robot_id"] = ids.index(owner) if owner is not None else None
        if "is_holding" in global_vars and global_vars["is_holding"] != (global_vars.get("holding_robot_id") is not None):
            raise NativeScopeError("is_holding and holding_robot_id disagree")
        x = []
        for robot in robots:
            position = _position(robot.position, "robot.position", (3,))
            if "yaw_rad" not in robot.metadata:
                raise NativeScopeError("robot.metadata.yaw_rad is required; z is not heading")
            x.append(position[:2] + [_finite(robot.metadata["yaw_rad"], "yaw_rad")])
        tasks = []
        bound_targets: dict[str, list[float]] = {}
        for item in ready:
            primitive = item.metadata.get("native_task_type")
            if primitive not in TARGET_KEYS:
                raise NativeScopeError("unmapped native_task_type; supported: " + ", ".join(TARGET_KEYS))
            if primitive in {"Pick", "Place"} and not {"is_holding", "holding_robot_id"} <= global_vars.keys():
                raise NativeScopeError("Pick/Place require explicit is_holding and holding_robot_id")
            if primitive in {"Open", "Close"} and "furniture_open_state" not in global_vars:
                raise NativeScopeError("Open/Close require explicit furniture_open_state")
            target = _position(item.metadata.get("target_position", item.metadata.get("target_xy")),
                               "task target", (2, 3))[:2]
            key = TARGET_KEYS[primitive]
            if key in bound_targets and bound_targets[key] != target:
                raise NativeScopeError(f"upstream shares {key} across instances; distinct targets cannot be mapped")
            bound_targets[key] = target
            tasks.append({"task_id": item.task_id, "task_type": primitive,
                          "target": item.target_ref, "target_position": target})
        global_vars.update(bound_targets)
        return {
            "robot_ids": ids, "tasks": tasks, "capability_names": capabilities, "x": x,
            "A": [[int(c in r.capability.capabilities) for r in robots] for c in capabilities],
            "T": [[int(c in t.required_capabilities) for c in capabilities] for t in ready],
            "global_vars": global_vars,
            "time_s": _finite(package.metadata.get("time_s", 0.0), "time_s"),
        }



def _overlay_real_namespace(destination: Path, primary: Path, fallback: Path) -> None:
    """Build a disposable namespace from actual upstream files; never a mock package."""
    destination.mkdir(parents=True, exist_ok=True)
    names = set()
    if primary.exists():
        names.update(p.name for p in primary.iterdir())
    if fallback.exists():
        names.update(p.name for p in fallback.iterdir())
    for name in sorted(names):
        if name == "__pycache__" or name.startswith("."):
            continue
        first, second = primary / name, fallback / name
        chosen = first if first.exists() else second
        target = destination / name
        if chosen.is_dir():
            _overlay_real_namespace(target, first, second)
        else:
            target.symlink_to(chosen)


def _syntax_repair_advisory(root: Path) -> dict[str, Any]:
    """Describe, but never apply, the two exact upstream syntax repairs."""
    files = (
        "TeamWeaver/planner/HRCS/plan_module/execution_manager.py",
        "TeamWeaver/planner/miqp_planner/optimizer/adaptive_optimizer_v2.py",
    )
    entries = []
    for relative in files:
        path = root / relative
        original = path.read_text()
        replacement = original.replace("iflen(", "if len(")
        if replacement == original:
            continue
        entries.append({
            "file": relative,
            "applied": False,
            "condition": "Only an explicitly authorized temporary compatibility overlay; never the locked source checkout.",
            "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
            "hypothetical_patched_sha256": hashlib.sha256(replacement.encode()).hexdigest(),
            "diff": "".join(difflib.unified_diff(original.splitlines(True), replacement.splitlines(True),
                                                  fromfile=relative + " (original)",
                                                  tofile=relative + " (hypothetical iflen repair)")),
        })
    return {"applied": False, "files": entries,
            "source_lock_unchanged": True,
            "warning": "Advisory only. No patched source was imported or executed."}


def _validate_action_output(response, parsed, payload, tools_by_uid):
    """Reject-only EAI output seam after the original parser, never a planner.

    Match exact declared task/action arguments; do not pick a target, repair
    output, choose a coalition owner, or manufacture an AlgorithmDecision.
    Even an accepted receipt is only a dispatch candidate, not planner success.
    """
    errors, directives, candidates, task_annotations = [], {}, [], {}
    nodes = {n["name"]: n["type"] for n in payload["world_graph"]["nodes"]}
    robot_ids = payload["robot_ids"]
    parsed = {str(uid): value for uid, value in parsed.items()}
    completed = set(payload.get("completed_task_ids", ()))
    catalog = payload.get("task_catalog", ())
    catalog_ids = {t["task_id"] for t in catalog}

    def reject(code, **details):
        errors.append({"code": code, **details})

    # Only these argument schemas have an explicit semantic boundary here.
    # Unsupported native tools are rejected rather than guessed.
    target_types = {
        "Navigate": {"Room", "Furniture", "Object"}, "Explore": {"Room"},
        "Pick": {"Object"}, "Open": {"Furniture"}, "Close": {"Furniture"},
    }
    for line in response.splitlines():
        if "_Task" in line:
            annotation = re.fullmatch(r"\s*Agent_(\d+)_Task:\s*([^\s]+)\s*", line)
            if not annotation:
                reject("malformed_task_annotation")
            else:
                uid, task_id = int(annotation[1]), annotation[2]
                if uid in task_annotations or not 0 <= uid < len(robot_ids):
                    reject("duplicate_or_unknown_task_annotation_agent", agent_uid=uid)
                task_annotations[uid] = task_id
        if "_Action" not in line:
            continue
        match = re.fullmatch(r"\s*Agent_(\d+)_Action:\s*([A-Za-z][A-Za-z0-9_]*)\[([^\[\]\r\n]*)\]\s*", line)
        if not match:
            reject("malformed_action_directive")
            continue
        uid, name, text = int(match[1]), match[2], match[3]
        args = [part.strip() for part in text.split(",")] if text.strip() else []
        if uid in directives:
            reject("duplicate_agent_directive", agent_uid=uid)
        directives[uid] = (name, args)
        if not 0 <= uid < len(robot_ids):
            reject("unknown_agent", agent_uid=uid)

    if not completed <= catalog_ids:
        reject("unknown_completed_task")
    for uid, robot_id in enumerate(robot_ids):
        if uid not in directives:
            reject("missing_agent_directive", agent_uid=uid)
            continue
        name, args = directives[uid]
        native = parsed.get(str(uid))
        if not isinstance(native, (list, tuple)) or len(native) != 3 or native[2]:
            reject("native_parser_error", agent_uid=uid)
            continue
        native_args = [part.strip() for part in native[1].split(",")] if native[1] else []
        if native[0] != name or native_args != args:
            reject("native_parser_changed_directive", agent_uid=uid)
        if name not in tools_by_uid.get(str(uid), ()):
            reject("unavailable_tool", agent_uid=uid, action=name)
        if name == "Wait":
            if args:
                reject("wrong_argument_count", agent_uid=uid)
            if uid in task_annotations:
                reject("wait_cannot_claim_task", agent_uid=uid)
            continue  # Wait does not imply task completion or successful action.
        if name not in target_types:
            reject("unmapped_argument_schema", agent_uid=uid, action=name)
            continue
        if len(args) != 1:
            reject("wrong_argument_count", agent_uid=uid)
            continue
        if args[0] not in nodes:
            reject("unknown_entity", agent_uid=uid, target=args[0])
        elif nodes[args[0]] not in target_types[name]:
            reject("wrong_entity_type", agent_uid=uid, target=args[0])
        matches = [t for t in catalog if t["action"] == name and t["arguments"] == args]
        if uid in task_annotations:
            matches = [t for t in matches if t["task_id"] == task_annotations[uid]]
            if not matches:
                reject("task_annotation_mismatch", agent_uid=uid)
        if len(matches) != 1:
            reject("ambiguous_task_mapping" if matches else "unmapped_task", agent_uid=uid)
            continue
        task = matches[0]
        if task["task_id"] in completed or not set(task["prerequisites"]) <= completed:
            reject("task_not_ready", agent_uid=uid, task_id=task["task_id"])
        if not set(task["required_capabilities"]) <= set(payload["capabilities"][uid]):
            reject("capability_mismatch", agent_uid=uid, task_id=task["task_id"])
        candidates.append({"task_id": task["task_id"], "robot_id": robot_id,
                           "action": name, "arguments": args})
    for uid in parsed:
        if uid not in {str(i) for i in range(len(robot_ids))}:
            reject("unknown_native_agent", agent_uid=uid)
    for task_id in {c["task_id"] for c in candidates}:
        owners = [c["robot_id"] for c in candidates if c["task_id"] == task_id]
        if len(owners) > 1:
            reject("coalition_not_dispatchable", task_id=task_id, robot_ids=owners)
    return {"schema": "heterocoord.teamweaver.output_seam.v2",
            "status": "rejected" if errors else "boundary_valid_only",
            "errors": errors, "raw_response": response, "native_actions": parsed,
            "dispatch_candidates": [] if errors else candidates,
            "rejected_candidates": candidates if errors else [],
            "full_semantic_success": False, "assignments_created": False,
            "repairs_applied": False}


def _output_contract_text(payload, tools_by_uid):
    """Explicit, hashed prompt suffix: task/world input and reject-only contract."""
    contract = {
        "scope": "I/O boundary only; this does not choose allocations",
        "instruction": payload["instruction"],
        "entities": [{"name": n["name"], "type": n["type"]} for n in payload["world_graph"]["nodes"]],
        "version": "heterocoord.teamweaver.output_seam.v2",
        "agents": [{"native_uid": i, "robot_id": rid, "tools": tools_by_uid[str(i)],
                    "capabilities": payload["capabilities"][i]}
                   for i, rid in enumerate(payload["robot_ids"])],
        "task_action_catalog": payload["task_catalog"],
        "rules": ["Use exact entity identifiers; never guess or rename them.",
                  "Emit one Agent_<uid>_Action: Tool[arguments] directive per agent.",
                  "For every non-Wait action, also emit Agent_<uid>_Task: <exact task_id> before Assigned! This is a task identity annotation; choose the task yourself from the catalog. Do not annotate Wait.",
                  "Choose only an exact declared task action, or Wait[] without claiming completion.",
                  "Unsupported, ambiguous, duplicate or coalition task outputs will be rejected.",
                  "This boundary supplies no simulated execution feedback."],
    }
    return "\nEAI input/output contract (adapter addition):\n" + json.dumps(contract, ensure_ascii=False, sort_keys=True)


def _prepare_semantic_mapping(root: Path, payload: Mapping[str, Any], *, runtime=None) -> dict[str, Any]:
    """Construct real upstream WorldGraph/Agent objects and render the real prompt.

    This receipt is decision-free. It proves the EAI boundary can be expressed
    in TeamWeaver's native semantic objects while planner construction and LLM
    calls remain governed by the original code.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("semantic_input must be a mapping")
    instruction = payload.get("instruction")
    graph_data = payload.get("world_graph")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("semantic input requires metadata.instruction")
    if not isinstance(graph_data, Mapping):
        raise ValueError("semantic input requires metadata.teamweaver_world_graph")
    from habitat_llm.world_model import (Furniture, Human, Object, Receptacle,
                                         Room, SpotRobot, WorldGraph)
    from habitat_llm.llm.instruct.utils import get_world_descr, actions_parser
    from habitat_llm.agent import Agent
    from omegaconf import OmegaConf

    classes = {"Room": Room, "Furniture": Furniture, "Object": Object,
               "Receptacle": Receptacle, "SpotRobot": SpotRobot, "Human": Human}
    nodes: dict[str, Any] = {}
    graph = WorldGraph()
    raw_nodes = graph_data.get("nodes", ())
    if not isinstance(raw_nodes, (list, tuple)) or not raw_nodes:
        raise ValueError("teamweaver_world_graph.nodes must be a nonempty sequence")
    for item in raw_nodes:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            raise ValueError("world graph nodes require name and type")
        entity_type = item.get("type", "Object")
        if entity_type not in classes:
            raise ValueError(f"unsupported TeamWeaver world graph entity type: {entity_type}")
        entity = classes[entity_type](item["name"], dict(item.get("properties", {})))
        graph.add_node(entity)
        nodes[entity.name] = entity
    for edge in graph_data.get("edges", ()):
        if not isinstance(edge, Mapping) or edge.get("from") not in nodes or edge.get("to") not in nodes:
            raise ValueError("world graph edges must reference declared nodes")
        graph.add_edge(nodes[edge["from"]], nodes[edge["to"]], str(edge["label"]),
                       opposite_label=edge.get("opposite_label"))

    tool_names = payload.get("tool_names", ("oracle_nav", "oracle_pick", "oracle_place",
                                               "oracle_open", "oracle_close", "wait"))
    tools = {}
    for name in tool_names:
        if not isinstance(name, str) or not name.replace("_", "").isalnum():
            raise ValueError("tool names must be original configuration stems")
        config_path = root / "TeamWeaver/conf/tools/motor_skills" / f"{name}.yaml"
        if not config_path.exists():
            raise ValueError(f"original tool config not found: {config_path}")
        tools.update(OmegaConf.to_container(OmegaConf.load(config_path)))
    agent_conf = OmegaConf.create({"tools": {"native_tools": tools}})
    # Native MotorSkillTool construction/descriptions do not need a simulator.
    # This category avoids motor binding; no skill execution/grammar is claimed.
    agents = [Agent(i, agent_conf, env_interface=None) for i in range(len(payload["robot_ids"]))]
    agent = agents[0]
    from habitat_llm.agent.env import EnvironmentInterface
    prompt_path = root / "TeamWeaver/conf/instruct/few_shot_centralized_full_obs_multiagent.yaml"
    prompt_conf = OmegaConf.load(prompt_path)
    original_prompt = str(prompt_conf.prompt).format(input=instruction,
                                                     tool_descriptions=agent.tool_descriptions)
    tools_by_uid = {str(a.uid): sorted(a.tools) for a in agents}
    prompt = original_prompt
    suffix = ""
    if payload.get("output_seam"):
        suffix = _output_contract_text(payload, tools_by_uid)
        prompt += suffix
    credentials = {name: bool(os.environ.get(name)) for name in (
        "DONGLI_API_KEY", "DONGLI_BASE_URL", "DONGLI_MODEL", "DONGLI_REASONING_EFFORT")}
    result = {
        "instruction": instruction,
        "world_graph": {"node_names": sorted(nodes), "edge_count": len(graph_data.get("edges", ())),
                         "file": str(Path(sys.modules[WorldGraph.__module__].__file__).resolve()),
                         "nodes": [{"name": n.name, "type": type(n).__name__,
                                    "properties": n.properties} for n in nodes.values()],
                         "world_description": get_world_descr(graph, centralized=True,
                                                                include_room_name=True)},
        "agents": [{"uid": a.uid, "robot_id": robot_id, "tool_names": sorted(a.tools),
                    "tool_descriptions": a.tool_descriptions, "dry_run": a._dry_run}
                   for a, robot_id in zip(agents, payload["robot_ids"])],
        "environment_interface": {
            "class": EnvironmentInterface.__module__ + "." + EnvironmentInterface.__name__,
            "file": str(Path(sys.modules[EnvironmentInterface.__module__].__file__).resolve()),
            "imported": True, "instantiated": False,
            "reason": "Original constructor requires a real Habitat environment/simulator; none supplied.",
        },
        "motor_skills_bound": False, "tool_grammar_verified": False,
        "prompt": {"source": str(prompt_path),
                   "sha256": hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
                   "original_sha256": hashlib.sha256(original_prompt.encode()).hexdigest(),
                   "rendered_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                   "output_seam": {"enabled": bool(payload.get("output_seam")),
                                   "upstream_template_modified": False,
                                   "suffix": suffix,
                                   "suffix_sha256": hashlib.sha256(suffix.encode()).hexdigest()},
                   "rendered": prompt},
        "credentials_present": credentials, "llm_calls_made": False,
    }
    if "parser_probe_response" in payload:
        parsed = actions_parser(agents, payload["parser_probe_response"])
        result["parser_probe"] = {
            "input_source": "supplied_boundary_test_not_llm_generation",
            "raw_actions": parsed,
            "robot_actions": {payload["robot_ids"][uid]: action for uid, action in parsed.items()},
            "assignments_created": False,
        }
        if payload.get("output_seam"):
            result["parser_probe"]["output_seam"] = _validate_action_output(
                payload["parser_probe_response"], parsed, payload, tools_by_uid)
    if payload.get("live_llm_probe"):
        result["llm_boundary_probe"] = _live_original_action_llm(
            root, prompt, prompt_conf.stopword, agents, payload)
        boundary = result["llm_boundary_probe"]
        # Validation is evidence only: preserve the entire response and never
        # repair native actions into a synthetic executable plan.
        boundary["unknown_single_target_entities"] = [
            {"agent_uid": uid, "action": action[0], "target": action[1]}
            for uid, action in boundary.get("raw_actions", {}).items()
            if action[0] in {"Navigate", "Pick", "Open", "Close"} and action[1] not in nodes]
        boundary["unavailable_tools"] = [
            {"agent_uid": uid, "action": action[0]}
            for uid, action in boundary.get("raw_actions", {}).items()
            if action[0] not in agents[uid].tools]
        result["llm_calls_made"] = result["llm_boundary_probe"]["request_attempted"]
    if runtime is not None:
        runtime.update(graph=graph, agents=agents, output_suffix=suffix, tools_by_uid=tools_by_uid)
    return result


def _live_original_action_llm(root, prompt, stopword, agents, payload):
    """Run unchanged OpenAIChat.generate; adapt only its API transport/config.

    This independent boundary probe cannot replace LLMPlanner.replan or certify
    decomposition/MIQP. Original package imports, prompt and parser execute.
    """
    from urllib.parse import urlsplit
    from types import SimpleNamespace
    from openai import OpenAI
    from omegaconf import OmegaConf
    from habitat_llm.llm.openai_chat import OpenAIChat
    from habitat_llm.llm.instruct.utils import actions_parser

    required = ("DONGLI_API_KEY", "DONGLI_BASE_URL", "DONGLI_MODEL", "DONGLI_REASONING_EFFORT")
    absent = [name for name in required if not os.environ.get(name)]
    receipt = {"scope": "original_action_llm_and_parser_boundary_only",
               "request_attempted": False, "native_replan_invoked": False,
               "assignments_created": False, "missing_configuration": absent}
    if absent:
        receipt["status"] = "configuration_missing"
        return receipt
    base_url = os.environ["DONGLI_BASE_URL"]
    model = os.environ["DONGLI_MODEL"]
    effort = os.environ["DONGLI_REASONING_EFFORT"]
    address = urlsplit(base_url)
    if address.scheme != "https" or not address.hostname or address.username or address.password or address.query:
        raise NativeScopeError("Dongli base URL must be HTTPS without credentials/query")
    config_path = root / "TeamWeaver/conf/llm/openai_chat.yaml"
    conf = OmegaConf.load(config_path)
    conf.generation_params.model = model  # User-authorized model/API configuration.
    receipt.update(requested_model=model, reasoning_effort=effort, base_url=base_url,
                   llm_file=str(Path(sys.modules[OpenAIChat.__module__].__file__).resolve()),
                   config_source=str(config_path), config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
                   prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())

    class APITransport:
        """Real SDK completion transport, adding only authorized API parameters."""
        def __init__(self, client):
            self.client = client
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            receipt["request_attempted"] = True
            response = self.client.chat.completions.create(
                **kwargs, extra_body={"reasoning_effort": effort})
            receipt.update(returned_model=response.model, response_id=response.id,
                           usage=response.usage.model_dump() if response.usage else None,
                           raw_response=response.choices[0].message.content,
                           finish_reason=response.choices[0].finish_reason)
            return response

    # Execute the original constructor, with real credentials. Its Azure client
    # is replaced at the explicit API boundary by a real OpenAI-compatible SDK.
    previous = {name: os.environ.get(name) for name in ("OPENAI_API_KEY", "OPENAI_ENDPOINT")}
    sdk = None
    try:
        os.environ["OPENAI_API_KEY"] = os.environ["DONGLI_API_KEY"]
        os.environ["OPENAI_ENDPOINT"] = address.netloc
        llm = OpenAIChat(conf)
        llm.client.close()
        sdk = OpenAI(api_key=os.environ["DONGLI_API_KEY"], base_url=base_url,
                     max_retries=0, timeout=45)
        llm.client = APITransport(sdk)
        response = llm.generate(prompt, stopword)
        parsed = actions_parser(agents, response)
        robot_ids = payload["robot_ids"]
        receipt.update(status="returned", native_generated_text=response, raw_actions=parsed,
                       robot_actions={robot_ids[uid]: action for uid, action in parsed.items()})
        if payload.get("output_seam"):
            receipt["output_seam"] = _validate_action_output(
                response, parsed, payload, {str(a.uid): sorted(a.tools) for a in agents})
    except Exception as exc:
        # HTTP bodies and exception messages may contain credentials; never expose them.
        receipt.update(status="failed", error_type=type(exc).__name__,
                       http_status=getattr(exc, "status_code", None))
    finally:
        if sdk is not None:
            sdk.close()
        for name, old_value in previous.items():
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value
    return receipt


def _map_semantic_input(package, robots) -> dict[str, Any]:
    from task.contracts import EaiTaskPackage
    if not isinstance(package, EaiTaskPackage) or not package.graph.valid:
        raise NativeScopeError("a valid EaiTaskPackage is required")
    robot_ids = [r.capability.robot_id for r in robots]
    if len(robot_ids) != len(set(robot_ids)) or set(robot_ids) != {
            r.capability.robot_id for r in package.robots}:
        raise NativeScopeError("runtime/package robot roster mismatch")
    if len(robots) != 2 or not all(r.capability.active for r in robots):
        raise NativeScopeError("native semantic planner has two fixed roles; require two active robots")
    metadata = dict(package.metadata)
    if "instruction" not in metadata:
        raise NativeScopeError("full_semantic requires package.metadata.instruction")
    if "teamweaver_world_graph" not in metadata:
        raise NativeScopeError("full_semantic requires package.metadata.teamweaver_world_graph")
    if not isinstance(metadata["teamweaver_world_graph"], Mapping):
        raise NativeScopeError("teamweaver_world_graph must be a mapping")
    world = json.loads(json.dumps(metadata["teamweaver_world_graph"], allow_nan=False))
    if world.get("coordinate_frame") != "habitat_y_up":
        raise NativeScopeError("teamweaver_world_graph.coordinate_frame must explicitly be habitat_y_up")
    node_names = [node["name"] for node in world.get("nodes", ())]
    if len(node_names) != len(set(node_names)):
        raise NativeScopeError("world graph node names must be unique")
    for i, robot in enumerate(robots):
        matches = [n for n in world.get("nodes", ()) if n["name"] == f"agent_{i}"]
        if len(matches) != 1:
            raise NativeScopeError("world graph must supply agent_0 and agent_1 nodes")
        position = _position(robot.metadata.get("teamweaver_position"),
                             "robot.metadata.teamweaver_position (Habitat y-up)", (3,))
        matches[0].setdefault("properties", {})["translation"] = position
    task_catalog = []
    for item in package.graph.tasks:
        action = item.metadata.get("native_task_type")
        if not isinstance(action, str):
            raise NativeScopeError(f"semantic task {item.task_id} requires metadata.native_task_type")
        if action == "Wait":
            arguments = []
        elif isinstance(item.target_ref, str) and item.target_ref:
            arguments = [item.target_ref]
        else:
            raise NativeScopeError(f"semantic task {item.task_id} requires target_ref for output mapping")
        task_catalog.append({"task_id": item.task_id, "action": action, "arguments": arguments,
                             "prerequisites": list(item.prerequisites),
                             "required_capabilities": sorted(item.required_capabilities)})
    return {"instruction": metadata["instruction"],
            "world_graph": world,
            "tool_names": metadata.get("teamweaver_tool_names", (
                "oracle_nav", "oracle_pick", "oracle_place", "oracle_open", "oracle_close", "wait")),
            "robot_ids": robot_ids,
            "capabilities": [sorted(r.capability.capabilities) for r in robots],
            "task_catalog": task_catalog}

def _native_worker(payload: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(payload["source_root"]).resolve()
    hrcs = root / "TeamWeaver/planner/HRCS"
    sys.path.insert(0, str(hrcs))  # Original newTask.py uses these same namespaces.
    if payload["operation"] in {"probe_full_semantic", "prepare_full_semantic", "run_full_semantic"}:
        missing = [name for name in ("torch", "habitat", "habitat_sim")
                   if importlib.util.find_spec(name) is None]
        partnr = Path(payload.get("partnr_root", os.environ.get(
            "HETEROCOORD_TEAMWEAVER_PARTNR_ROOT",
            "/home/airs/.cache/heterocoord/upstream/teamweaver-partnr"))) / "habitat_llm"
        with tempfile.TemporaryDirectory(prefix="teamweaver-namespace-") as alias:
            repair = {"applied": False}
            if payload.get("source_compatibility", True):
                spec = importlib.util.spec_from_file_location("teamweaver_source_compat", Path(__file__).with_name("source_compat.py"))
                compatibility = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(compatibility)
                patched = Path(alias) / "compat"
                repair = compatibility.apply_compatibility_overlay(root, patched)
                repair["applied"] = True
                repair["adapter_module_sha256"] = hashlib.sha256(Path(__file__).with_name("source_compat.py").read_bytes()).hexdigest()
                root = patched
                sys.path.insert(0, str(root / "TeamWeaver/planner/HRCS"))
            _overlay_real_namespace(Path(alias) / "habitat_llm", root / "TeamWeaver", partnr)
            sys.path.insert(0, alias)
            mapping = None
            stage = "semantic_input_mapping" if payload.get("semantic_input") else "planner_import"
            try:
                semantic_input = payload.get("semantic_input")
                runtime = {}
                mapping = _prepare_semantic_mapping(root, semantic_input, runtime=runtime) if semantic_input else None
                stage = "planner_import"
                module = importlib.import_module(SEMANTIC_MODULE)
            except Exception as exc:
                result = {"full_semantic_status": "blocked", "import_error": type(exc).__name__,
                          "blocked_stage": stage, "planner_entrypoint": SEMANTIC_MODULE + ".LLMPlanner.replan",
                          "native_replan_invoked": False, "semantic_solver_invoked": False,
                          "llm_calls_made": False,
                          "missing_module": getattr(exc, "name", None),
                          "missing_runtime_packages": missing,
                          "source_compatibility": repair,
                          "namespace_overlay": "real TeamWeaver/PARTNR files only; no mocks"}
                if isinstance(exc, SyntaxError):
                    result["planner_import"] = {"error_type": type(exc).__name__,
                        "file": str(Path(exc.filename).resolve()), "line": exc.lineno,
                        "message": exc.msg, "source_line": (exc.text or "").strip()}
                    result["planner_import"]["sha256"] = hashlib.sha256(
                        Path(exc.filename).read_bytes()).hexdigest()
                    result["syntax_repair_advisory"] = _syntax_repair_advisory(root)
                else:
                    result[stage] = {"error_type": type(exc).__name__,
                                     "missing_module": getattr(exc, "name", None)}
                if mapping is not None:
                    result["semantic_mapping"] = mapping
                    result["llm_calls_made"] = mapping["llm_calls_made"]
                return result
            if payload["operation"] == "run_full_semantic":
                spec = importlib.util.spec_from_file_location("teamweaver_semantic_runtime", Path(__file__).with_name("semantic_runtime.py"))
                implementation = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(implementation)
                executed = implementation.run_native_planner(root, semantic_input, runtime)
                if executed.get("parsed_actions") is not None:
                    executed["output_validation"] = _validate_action_output(executed["response"], executed["parsed_actions"], semantic_input, runtime["tools_by_uid"])
                return {"full_semantic_status": "native_planner_executed", "source_compatibility": repair,
                        "semantic_mapping": mapping, "native_planner": executed,
                        "native_replan_invoked": executed["native_replan_invoked"],
                        "semantic_solver_invoked": executed["semantic_solver_invoked"]}
            return {"full_semantic_status": "importable", "planner_file": str(Path(module.__file__).resolve()),
                    "source_compatibility": repair,
                    "native_replan_invoked": False, "llm_calls_made": bool(mapping and mapping["llm_calls_made"]),
                    "namespace_overlay": "real TeamWeaver/PARTNR files only; no mocks",
                    "semantic_mapping": mapping}

    import numpy as np
    import cvxpy as cp
    import gurobipy
    from class_def.RTA_task import RTA
    from params_module.scenario_params_task import ScenarioConfigTask
    from params_module.opt_params_task import OptimizationConfigTask

    n_r, n_t = len(payload["robot_ids"]), len(payload["tasks"])
    n_c = len(payload["capability_names"])
    scenario = ScenarioConfigTask(n_r=n_r)
    params = scenario.get_scenario_params()
    params["A"] = np.array(payload["A"], dtype=float)
    params["Hs"] = [np.eye(n_c)[i:i + 1] for i in range(n_c)]
    params["ws"] = [np.eye(1) for _ in range(n_c)]
    # Static template rows belong to the original 13 task utilities. The solve
    # itself uses the instance T below. This is input data, not a patched solver.
    params["T"] = np.zeros((len(params["tasks"]), n_c))
    for item, required in zip(payload["tasks"], payload["T"]):
        index = next(i for i, t in enumerate(params["tasks"]) if t["name"] == item["task_type"])
        params["T"][index] = np.maximum(params["T"][index], required)
    opt = OptimizationConfigTask(n_r=n_r)
    rta = RTA(params, opt.get_opt_params())
    global_vars = scenario.get_global_task_vars().copy()
    global_vars.update(payload["global_vars"])
    for key in TARGET_KEYS.values():
        if key in global_vars:
            global_vars[key] = np.asarray(global_vars[key], dtype=float)
    bounds = opt.get_instance_robot_bounds(payload["tasks"])
    phase = {"T": np.array(payload["T"], dtype=float), "n_r_bounds": bounds,
             "tasks": payload["tasks"], "aptitude_matrix": None}
    alpha, u, delta, duration, status = rta.solve_miqp_phase_aware(
        np.asarray(payload["x"], dtype=float).T, payload["time_s"], phase, n_t, global_vars)
    return {
        "entrypoint": ENTRYPOINT, "status": status, "solve_time_s": duration,
        "robot_ids": payload["robot_ids"], "task_ids": [t["task_id"] for t in payload["tasks"]],
        "alpha": np.asarray(alpha).reshape(n_r, n_t).tolist(),
        "u": np.asarray(u).tolist(), "delta": np.asarray(delta).tolist(),
        "native_robot_bounds": bounds.tolist(), "F": params["F"].tolist(),
        "solver_file": str(Path(sys.modules[RTA.__module__].__file__).resolve()),
        "versions": {"numpy": np.__version__, "cvxpy": cp.__version__,
                     "gurobi": list(gurobipy.gurobi.version())},
        "fallback_used": False,
    }


def _main() -> None:
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("Use NativeTeamWeaverAdapter or records/algorithm_adapters/teamweaver/run_native.py")
    payload = json.load(sys.stdin)
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = _native_worker(payload)
        # Upstream can swallow missing utility data and return zero CBF terms.
        # Refuse that output without rewriting its exception/fallback behavior.
        result["native_error_count"] = log.getvalue().count("[ERROR]")
        if "native_planner" in result:
            if result["native_error_count"]:
                result["native_planner"]["planning_success"] = False
            diagnostics = [line for line in log.getvalue().splitlines() if "[ERROR]" in line]
            for key, value in os.environ.items():
                if value and any(token in key.upper() for token in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                    diagnostics = [line.replace(value, "[REDACTED]") for line in diagnostics]
            result["native_error_diagnostics"] = [line[:500] for line in diagnostics]
    except Exception as exc:
        result = {"error": type(exc).__name__, "missing_module": getattr(exc, "name", None)}
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    _main()
