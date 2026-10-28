"""Run the original Python AsynchTaskAllocation FMC_ATA in a disposable process.

Supported domain: one or more static, independent, single-mission tasks, explicitly declaring
``max_players=1``; idle single-ability robots; perfect native communication.
Task metadata ``fmc_ata`` requires position (3D), workload, importance and
max_players. Robot metadata ``fmc_ata`` requires speed, productivity and
status="idle". Package metadata ``fmc_ata.domain`` must equal SUPPORTED_DOMAIN.
No geometry scaling, utility replacement, allocation rounding or fallback is
performed. Native market fractions, normalized allocations, ordered schedules,
message counters and final received message contexts are returned in provenance.

The adapter dispatches only a complete native single-player schedule. Cooperative
fractional missions and DAGs cannot be represented by TaskRunner's Assignment.
The per-robot order is preserved; cross-robot timing is executor-owned. This is
native algorithm evidence, never physical simulation or task-success evidence.
"single_mission" refers to each task, not the number of tasks in the package;
all tasks enter one native solve and each robot can retain a multi-task schedule.

Dependencies (isolated venv): numpy, pandas (the original static driver imports
its reporting modules). Override FMC_ATA_SOURCE_ROOT / FMC_ATA_PYTHON or pass
source_root / python_executable explicitly. The parent owns source attestation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState


SUPPORTED_DOMAIN = "independent_single_mission_single_player"
DEFAULT_SOURCE_ROOT = Path("/home/airs/.cache/heterocoord/upstream/fmc-ata")
DEFAULT_PYTHON = Path("/home/airs/.cache/heterocoord/envs/fmc-ata/bin/python")


class NativeFMCATAError(RuntimeError):
    """No assignments are emitted on native, lifecycle or representation failure."""

    def __init__(self, message: str, *, evidence: dict | None = None):
        super().__init__(message)
        self.evidence = evidence or {}
        # Shared CLI's exception-output protocol; identical evidence, no new solve.
        self.native_result = self.evidence


class UnsupportedFMCATADomain(ValueError):
    """Input cannot be faithfully expressed in the supported native domain."""


class NativeFMCATAScheduleError(NativeFMCATAError):
    """Native solve returned, but its asynchronous schedule is not dispatchable.

    The unmodified driver can stop before final schedule messages are consumed.
    ``evidence`` retains that output; the adapter never retries or repairs it.
    """


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnsupportedFMCATADomain(f"{name} must be a finite positive number")
    if not math.isfinite(value) or value <= 0:
        raise UnsupportedFMCATADomain(f"{name} must be a finite positive number")
    return float(value)


def _position(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise UnsupportedFMCATADomain(f"{name} requires three coordinates")
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or
           not math.isfinite(x) for x in value):
        raise UnsupportedFMCATADomain(f"{name} requires finite coordinates")
    return list(value)


class NativeFMCATAAdapter:
    """A fresh native solver per call, with a hard wall timeout and full reaping."""

    def __init__(self, source_root: str | Path | None = None, *,
                 python_executable: str | Path | None = None,
                 timeout_seconds: float = 30.0, max_nclo: int = 10000):
        self.source_root = Path(source_root or os.environ.get(
            "FMC_ATA_SOURCE_ROOT", DEFAULT_SOURCE_ROOT)).expanduser().resolve()
        # Do not resolve the venv symlink: the path selects its isolated packages.
        self.python_executable = str(Path(python_executable or os.environ.get(
            "FMC_ATA_PYTHON", DEFAULT_PYTHON)).expanduser().absolute())
        self.timeout_seconds = _positive(timeout_seconds, "timeout_seconds")
        if isinstance(max_nclo, bool) or not isinstance(max_nclo, int) or max_nclo < 1:
            raise ValueError("max_nclo must be a positive integer")
        self.max_nclo = max_nclo

    def _encode(self, task: EaiTaskPackage, robots: Sequence[RobotState]) -> dict:
        package_cfg = task.metadata.get("fmc_ata", {})
        if package_cfg.get("domain") != SUPPORTED_DOMAIN:
            raise UnsupportedFMCATADomain(f"declare fmc_ata.domain={SUPPORTED_DOMAIN}")
        if (set(package_cfg) - {"domain", "communication"} or
                package_cfg.get("communication", "perfect") != "perfect"):
            raise UnsupportedFMCATADomain("only static tasks and perfect communication are supported")
        if not task.graph.valid:
            raise UnsupportedFMCATADomain("invalid task graph")
        task_ids = [item.task_id for item in task.graph.tasks]
        if not task_ids or len(task_ids) != len(set(task_ids)):
            raise UnsupportedFMCATADomain("nonempty unique task graph required")
        robot_ids = [r.capability.robot_id for r in robots]
        if len(robot_ids) != len(set(robot_ids)):
            raise UnsupportedFMCATADomain("unique runtime robot roster required")
        if ({r.capability.robot_id: r for r in robots}
                != {r.capability.robot_id: r for r in task.robots}):
            raise UnsupportedFMCATADomain("runtime roster differs from task package; bind_runtime first")
        active = [r for r in robots if r.capability.active]
        ids = [r.capability.robot_id for r in active]
        if not active or len(set(ids)) != len(ids):
            raise UnsupportedFMCATADomain("nonempty unique active robot roster required")
        players = []
        for i, robot in enumerate(active, 1):
            cfg = robot.metadata.get("fmc_ata", {})
            if set(cfg) - {"speed", "productivity", "status"}:
                raise UnsupportedFMCATADomain("unsupported robot fmc_ata fields")
            if len(robot.capability.capabilities) != 1 or cfg.get("status") != "idle":
                raise UnsupportedFMCATADomain("only idle, single-ability robots are supported")
            players.append({
                "id": -i, "robot_id": robot.capability.robot_id,
                "ability": next(iter(robot.capability.capabilities)),
                "position": _position(robot.position, "robot.position"),
                "speed": _positive(cfg.get("speed"), "robot.speed"),
                "productivity": _positive(cfg.get("productivity"), "robot.productivity"),
            })
        tasks = []
        for i, item in enumerate(task.graph.tasks, 1):
            cfg = item.metadata.get("fmc_ata", {})
            if set(cfg) - {"position", "workload", "importance", "max_players"}:
                raise UnsupportedFMCATADomain("unsupported task fmc_ata fields")
            if item.prerequisites or len(item.required_capabilities) != 1:
                raise UnsupportedFMCATADomain("only independent single-ability tasks are supported")
            if type(cfg.get("max_players")) is not int or cfg["max_players"] != 1:
                raise UnsupportedFMCATADomain("explicit max_players=1 required; fractions cannot be rounded")
            ability = next(iter(item.required_capabilities))
            if ability not in {p["ability"] for p in players}:
                raise UnsupportedFMCATADomain(f"no native player for task ability {ability!r}")
            tasks.append({
                "id": i, "task_id": item.task_id, "mission_id": i,
                "ability": ability, "max_players": 1,
                "position": _position(cfg.get("position"), "task.position"),
                "workload": _positive(cfg.get("workload"), "task.workload"),
                "importance": _positive(cfg.get("importance"), "task.importance"),
            })
        return {"source_root": str(self.source_root), "max_nclo": self.max_nclo,
                "players": players, "tasks": tasks}

    def _run_native(self, payload: dict) -> dict:
        required = ("solver_fmc_distributed_asy.py", "1_simulation_user_static.py")
        if any(not (self.source_root / name).is_file() for name in required):
            raise NativeFMCATAError("Python AsynchTaskAllocation source_root is unavailable")
        if not Path(self.python_executable).is_file():
            raise NativeFMCATAError("isolated FMC-ATA Python executable is unavailable")
        with tempfile.TemporaryDirectory(prefix="heterocoord-fmc-ata-") as directory:
            root = Path(directory)
            input_path, output_path = root / "input.json", root / "output.json"
            diagnostic_path = root / "diagnostic.json"
            worker_payload = {**payload, "diagnostic_path": str(diagnostic_path),
                              "diagnostic_after_s": self.timeout_seconds * .7}
            input_path.write_text(json.dumps(worker_payload, allow_nan=False), encoding="utf-8")
            started = time.monotonic()
            with (root / "worker.log").open("w+b") as log:
                process = subprocess.Popen(
                    [self.python_executable, "-I", "-B", str(Path(__file__).resolve()),
                     "--native-worker", str(input_path), str(output_path)],
                    cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True,
                )
                try:
                    process.wait(timeout=self.timeout_seconds)
                except BaseException as exc:
                    # Native mailer/agent threads may block; kill their entire process.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    if isinstance(exc, subprocess.TimeoutExpired):
                        log.seek(0)
                        diagnostic = (json.loads(diagnostic_path.read_text())
                                      if diagnostic_path.is_file() else None)
                        raise NativeFMCATAError("native solver wall timeout; worker killed and reaped",
                            evidence={"native_execution": "timeout", "worker_pid": process.pid,
                                      "worker_reaped": True, "simulation_evidence": "not_run",
                                      "wall_seconds": time.monotonic() - started,
                                      "diagnostic_snapshot": diagnostic,
                                      "worker_log_tail": log.read()[-24000:].decode(errors="replace"),
                                      "assignments_emitted": False}) from exc
                    raise
            lifecycle = {"worker_pid": process.pid, "worker_returncode": process.returncode,
                         "worker_reaped": True, "wall_seconds": time.monotonic() - started}
            if process.returncode != 0 or not output_path.is_file():
                lifecycle["worker_log_tail"] = (root / "worker.log").read_bytes()[-24000:].decode(errors="replace")
                raise NativeFMCATAError("native worker failed; no decision emitted", evidence=lifecycle)
            raw = json.loads(output_path.read_text(encoding="utf-8"))
            raw["lifecycle"] = lifecycle
            if raw.get("error"):
                raise NativeFMCATAError(raw["error"], evidence=raw)
            return raw

    def __call__(self, task: EaiTaskPackage, robots: Sequence[RobotState]) -> AlgorithmDecision:
        from algorithm.common_coordination.contract import AlgorithmCondition, Assignment
        from task.contracts import AlgorithmDecision

        payload = self._encode(task, robots)
        raw = self._run_native(payload)
        assignments = []
        seen = set()
        task_inputs = {t["id"]: t for t in payload["tasks"]}
        player_inputs = {p["id"]: p for p in payload["players"]}
        task_outputs = {t["id"]: t for t in raw["tasks"]}
        output_players = [p["id"] for p in raw["players"]]
        output_tasks = [t["id"] for t in raw["tasks"]]
        if (len(output_players) != len(set(output_players)) or set(output_players) != set(player_inputs)
                or len(output_tasks) != len(set(output_tasks)) or set(output_tasks) != set(task_inputs)):
            raise NativeFMCATAScheduleError("native output entity roster differs from input", evidence=raw)
        if not raw["all_tasks_converged"] or raw["nclo_limit_reached"]:
            raise NativeFMCATAError("native solve did not converge within NCLO budget", evidence=raw)
        for player in raw["players"]:
            player_input = player_inputs[player["id"]]
            for entry in player["schedule"]:
                if entry["task_id"] not in task_inputs:
                    raise NativeFMCATAScheduleError("native schedule references unknown task", evidence=raw)
                item = task_inputs[entry["task_id"]]
                task_allocation = task_outputs[entry["task_id"]]["x_jk_normal"].get(str(item["mission_id"]))
                if not isinstance(task_allocation, dict) or set(task_allocation) - {str(i) for i in player_inputs}:
                    raise NativeFMCATAScheduleError("native allocation references unknown mission or player", evidence=raw)
                # Consume the native schedule, never select a max market fraction here.
                if (entry["task_id"] in seen or entry["mission_id"] != item["mission_id"] or
                    entry["normalized_allocation"] != 1 or
                    task_allocation.get(str(player["id"])) != 1 or
                    any(v not in (0, None) for k, v in task_allocation.items() if k != str(player["id"])) or
                    player_input["ability"] != item["ability"] or
                    not math.isclose(entry["duration"], item["workload"] / player_input["productivity"],
                                     rel_tol=1e-12, abs_tol=0.0)):
                    raise NativeFMCATAScheduleError("native schedule is not a whole single-player mission", evidence=raw)
                seen.add(entry["task_id"])
                assignments.append(Assignment(item["task_id"], player_input["robot_id"],
                                               source="native:solver_fmc_distributed_asy.FMC_ATA.schedule"))
        if seen != set(task_inputs):
            raise NativeFMCATAScheduleError("native schedule does not cover the declared workload", evidence=raw)
        return AlgorithmDecision(
            condition=AlgorithmCondition(
                algorithm_id="FMC-ATA", source_reference=str(self.source_root),
                implementation="upstream:solver_fmc_distributed_asy.FMC_ATA",
                execution_scope="native_allocation_and_schedule_only",
                planner="FMC_ATA(util_structure_level=1)",
                supported_primitives=(SUPPORTED_DOMAIN,),
                unsupported_dimensions=("cooperative_fractional_missions", "task_dependencies",
                                        "busy_or_multi_ability_robots", "physical_execution"),
            ),
            assignments=tuple(assignments), provenance={
                "adapter": "NativeFMCATAAdapter", "supported_domain": SUPPORTED_DOMAIN,
                "contract_evidence": "complete_whole_mission_schedule",
                "native_evidence": "original_solver_executed",
                "simulation_evidence": "not_run", "physical_evidence": "not_evaluated",
                "source_integrity_gate": "parent_owned_not_asserted_here",
                "schedule_semantics": "per_robot_order_preserved; cross_robot_timing_executor_owned",
                "native_input": payload, "native_output": raw,
            },
        )


def _native_worker(payload: dict) -> dict:
    """Input construction and output serialization only; no replacement algorithm."""
    import importlib
    import threading
    import faulthandler
    import traceback

    source = Path(payload["source_root"])
    sys.path.insert(0, str(source))
    components = importlib.import_module("simulation_abstract_components")
    solver_module = importlib.import_module("solver_fmc_distributed_asy")
    driver = importlib.import_module("1_simulation_user_static")
    communication = importlib.import_module("general_communication_protocols")
    utility = importlib.import_module("general_r_ij")
    # Original driver exposes this run parameter for its termination callback.
    driver.max_nclo_algo_run = payload["max_nclo"]
    ability_ids = {name: i for i, name in enumerate(sorted({p["ability"] for p in payload["players"]}))}
    players = [components.PlayerSimple(
        id_=p["id"], current_location=p["position"], speed=p["speed"],
        productivity=p["productivity"],
        abilities=[components.AbilitySimple(ability_type=ability_ids[p["ability"]])],
    ) for p in payload["players"]]
    tasks = []
    for t in payload["tasks"]:
        mission = components.MissionSimple(
            task_id=t["id"], mission_id=t["mission_id"], initial_workload=t["workload"],
            arrival_time_to_the_system=0, task_importance=t["importance"], max_players=t["max_players"],
            abilities=[components.AbilitySimple(ability_type=ability_ids[t["ability"]])],
        )
        native_task = components.TaskSimple(id_=t["id"], location=t["position"],
                                            importance=t["importance"], missions_list=[mission])
        native_task.create_neighbours_list(players_list=players)
        components.find_and_allocate_responsible_player(task=native_task, players=players)
        tasks.append(native_task)
    protocol = communication.CommunicationProtocolPerfectCommunication()
    protocol.set_seed(0)
    solver = solver_module.FMC_ATA(
        util_structure_level=1,
        f_termination_condition=driver.f_termination_condition_all_tasks_converged,
        f_global_measurements={},  # Original message counters remain enabled.
        f_communication_disturbance=protocol.get_communication_disturbance,
        future_utility_function=utility.calculate_rij_abstract,
        counter_of_converges=driver.counter_of_converges, Threshold=driver.Threshold,
        is_with_timestamp=False,
    )
    thread_errors = []
    def record_thread_error(args):
        thread_errors.append(type(args.exc_value).__name__)
        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = record_thread_error
    solver.add_players_list(players)
    solver.add_tasks_list(tasks)
    diagnostic_done = threading.Event()
    def observe_only():
        if diagnostic_done.wait(payload["diagnostic_after_s"]):
            return
        faulthandler.dump_traceback(all_threads=True)
        # Deliberately non-atomic, read-only diagnostics: never dispatch or
        # declare convergence from this snapshot, and never wake native agents.
        snapshot = {"scope": "read_only_nonatomic_timeout_diagnostic_not_a_decision",
            "nclo": solver.mailer.time_mailer.clock,
            "sent": solver.mailer.msg_sent_counter,
            "mailer_inbox_batches": len(solver.mailer.inbox.buffer),
            "pending_mailer_messages": len(solver.mailer.msg_box),
            "agents": [{"id": a.simulation_entity.id_, "alive": a.is_alive(),
                        "idle": a.is_idle, "inbox_batches": len(a.inbox.buffer)}
                       for a in solver.agents_algorithm],
            "task_phase_II": {str(a.simulation_entity.id_): a.is_finish_phase_II for a in solver.tasks_algorithm},
            "native_player_schedules": {str(a.simulation_entity.id_):
                [{"task_id": t.id_, "mission_id": m.mission_id, "duration": d}
                 for t, m, d in a.simulation_entity.schedule] for a in solver.players_algorithm}}
        path = Path(payload["diagnostic_path"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, allow_nan=False))
        temporary.replace(path)
    if payload.get("diagnostic_path"):
        threading.Thread(target=observe_only, daemon=True, name="eai_read_only_diagnostic").start()
    try:
        nclo = solver.solve(0)
    finally:
        diagnostic_done.set()
    if thread_errors or any(a.is_alive() for a in solver.agents_algorithm) or solver.mailer.is_alive():
        raise NativeFMCATAError("native thread error or incomplete shutdown")

    def scalarize(value):
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, dict):
            return {str(getattr(k, "mission_id", getattr(k, "id_", k))): scalarize(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [scalarize(v) for v in value]
        if isinstance(value, solver_module.Utility):
            return {"linear_utility": value.linear_utility, "ro": value.ro}
        raise TypeError(f"unserializable native output type: {type(value).__name__}")

    def message(msg):
        if msg is None:
            return None
        return {k: scalarize(getattr(msg, k)) for k in (
            "sender", "receiver", "information", "msg_time", "timestamp", "is_with_perfect_communication")}

    player_records = []
    for agent in solver.players_algorithm:
        player = agent.simulation_entity
        schedule = [{"task_id": t.id_, "mission_id": m.mission_id, "duration": duration,
                     "normalized_allocation": agent.x_i_norm[t][m]}
                    for t, m, duration in player.schedule]
        player_records.append({"id": player.id_, "schedule": schedule,
                               "x_i": scalarize(agent.x_i), "x_i_norm": scalarize(agent.x_i_norm),
                               "utility": scalarize(agent.r_i),
                               "scheduling_data": [{"task_id": a.task.id_, "mission_id": a.mission.mission_id,
                                                    "measure": a.measure_, "norm_xjk": a.norm_xjk,
                                                    "time_player_arrives": a.time_player_arrives,
                                                    "max_time_received": a.max_time_received}
                                                   for a in agent.allocations_data],
                               "last_received_messages": [message(m) for m in agent.msgs_from_tasks.values()]})
    task_records = [{"id": a.simulation_entity.id_, "x_jk": scalarize(a.x_jk),
                     "x_jk_normal": scalarize(a.x_jk_normal), "bids": scalarize(a.bids),
                     "price_current": scalarize(a.price_current), "price_delta": scalarize(a.price_delta),
                     "max_time_per_mission": scalarize(a.max_time_per_mission),
                     "player_greedy_arrive_dict": scalarize(a.player_greedy_arrive_dict),
                     "is_finish_phase_II": a.is_finish_phase_II,
                     "last_received_messages": [message(m) for m in a.msgs_from_players.values()]}
                    for a in solver.tasks_algorithm]
    imported = {}
    for module in tuple(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if path and Path(path).suffix == ".py" and Path(path).resolve().is_relative_to(source):
            path = Path(path).resolve()
            imported[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "solver_class": f"{type(solver).__module__}.{type(solver).__name__}",
        "solver_module_file": solver_module.__file__, "imported_source_sha256": imported,
        "python_executable": sys.executable,
        "parameters": {"util_structure_level": 1, "ro": solver.ro,
                       "Threshold": solver.Threshold, "counter_of_converges": solver.counter_of_converges,
                       "communication": "CommunicationProtocolPerfectCommunication", "is_with_timestamp": False},
        "nclo": nclo, "nclo_limit_reached": nclo >= payload["max_nclo"],
        "all_tasks_converged": all(a.is_finish_phase_II for a in solver.tasks_algorithm),
        "termination_semantics": "original_driver_phase_II_flags_or_nclo_limit; not_an_optimality_certificate",
        "players": player_records, "tasks": task_records,
        "messages": {"sent": solver.mailer.msg_sent_counter,
                     "received": solver.mailer.get_counter_sum_msg_received_counter_from_agents(),
                     "lost": solver.mailer.msg_not_delivered_loss_counter,
                     "timestamp_lost": solver.mailer.get_counter_sum_of_timestamp_loss_msgs_from_agents(),
                     "measurements": scalarize(solver.mailer.measurements),
                     "trace_scope": "native_counters_and_final_received_contexts_not_full_wire_trace"},
        "native_threads_joined": True,
    }


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--native-worker":
        raise SystemExit("internal native worker: --native-worker INPUT OUTPUT")
    try:
        result = _native_worker(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
    except Exception as exc:
        result = {"error": f"native worker failed: {type(exc).__name__}: {exc}"}
    Path(sys.argv[3]).write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
