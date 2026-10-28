"""Explicit host-side registration seam for room06 production execution.

This module contains no simulator assumptions and no winning policy.  A live
Isaac host supplies callbacks that read controller arrival, execute a real
skill session, perform capability admission, monitor holds, and expose D1/D4/D6
inputs.  The setup fails closed when any callback is absent; tests may inject
their own callbacks only in CPU fixtures.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Callable

from benchmark.room06_task_instance_v2 import logical_object_definitions
from benchmark.records_v3 import canonical_digest
from task.logical_objects import LogicalObjectRuntime
from task.contracts import AlgorithmDecision, EaiTaskPackage
from benchmark.adapters import DecisionProvider


class Room06RuntimeHostError(RuntimeError):
    """The host did not provide a complete real execution boundary."""


class Room06TaskDriver:
    """Thin driver from an algorithm-owned decision to observer-owned events.

    ``run_case`` is the host's real controller loop. It receives the frozen
    package, roster, benchmark ledger and collector, and must return only the
    assignment plus measured terminal inputs. This class deliberately does
    not choose a coalition, advance Isaac, or synthesize task events.
    """

    def __init__(self, *, run_case: Callable[..., Mapping[str, Any]]) -> None:
        if not callable(run_case):
            raise TypeError("room06 run_case callback is required")
        self._run_case = run_case

    def collect_room06(self, *, package: Mapping[str, Any],
                       instance_agent_map: Mapping[str, str], collector: Any) -> dict[str, Any]:
        result = self._run_case(
            package=package,
            instance_agent_map=dict(instance_agent_map),
            collector=collector,
        )
        if not isinstance(result, Mapping) or set(result) != {"assignment", "terminal_inputs"}:
            raise Room06RuntimeHostError(
                "room06 run_case must return assignment and measured terminal_inputs"
            )
        return dict(result)


class ControllerBackedRoom06TaskDriver:
    """Drive the observer from a host-owned real controller step loop.

    ``decide`` is the only algorithm boundary. The host ``step`` callback must
    advance the live simulator/controller and return the measured records for
    that tick. No callback result is interpreted as success by this class:
    completed skill events are authenticated by ``collector`` and terminal
    D1/D4/D6 values are passed through for their own validators.

    ``public_task`` must be assembled by the host from public task/map/roster
    data, not by copying evaluator metadata. The DecisionProvider receives
    only that typed task and its complete robot roster. The trusted host step
    receives the full AlgorithmDecision so per-task assignments are preserved.

    Step result schema::

        {"skill_events": [observed-skill-event]}

    Holds are read directly by the collector from the logical ledger. The
    clock is host-owned, and every step must advance exactly one registered
    tick so joint-hold observations cannot be skipped.
    """

    def __init__(self, *, decide: DecisionProvider,
                 public_task: EaiTaskPackage,
                 step: Callable[..., Mapping[str, Any]],
                 clock: Callable[[], int],
                 terminal_inputs: Callable[..., Mapping[str, Any]],
                 max_steps: int = 1_000_000) -> None:
        if any(not callable(cb) for cb in (decide, step, clock, terminal_inputs)):
            raise TypeError("room06 controller driver requires decide, step, clock and terminal_inputs")
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        if not isinstance(public_task, EaiTaskPackage):
            raise TypeError("public_task must be a separately assembled EaiTaskPackage")
        self._public_task = deepcopy(public_task)
        self._decide = decide
        self._step = step
        self._clock = clock
        self._terminal_inputs = terminal_inputs
        self._max_steps = max_steps
        self._started = False

    @staticmethod
    def _assignment(value: Any, roster: Mapping[str, str]) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {"tick", "assigned_robot_ids", "decision_digest"}:
            raise Room06RuntimeHostError("controller decision assignment schema mismatch")
        tick = value.get("tick")
        ids = value.get("assigned_robot_ids")
        digest = value.get("decision_digest")
        if type(tick) is not int or tick < 0 or not isinstance(ids, list):
            raise Room06RuntimeHostError("controller decision assignment has invalid tick or IDs")
        if any(not isinstance(item, str) for item in ids) or len(ids) != len(set(ids)) or any(item not in roster for item in ids):
            raise Room06RuntimeHostError("controller decision selected an unknown or duplicate robot")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise Room06RuntimeHostError("controller decision digest must be SHA-256")
        return {"tick": tick, "assigned_robot_ids": list(ids), "decision_digest": digest}

    def _tick(self):
        value = self._clock()
        if type(value) is not int or not 0 <= value <= 15000:
            raise Room06RuntimeHostError("host clock must be an integer within the task horizon")
        return value

    def collect_room06(self, *, package: Mapping[str, Any],
                       instance_agent_map: Mapping[str, str], collector: Any) -> dict[str, Any]:
        from .room06_runtime_hook import Room06RuntimeHookCollector
        if not isinstance(collector, Room06RuntimeHookCollector):
            raise Room06RuntimeHostError("driver requires the real room06 collector")
        if self._started:
            raise Room06RuntimeHostError("driver already started; no silent episode retry")
        self._started = True
        public_task = deepcopy(self._public_task)
        public_ids = {robot.capability.robot_id for robot in public_task.robots}
        if public_ids != set(instance_agent_map):
            raise Room06RuntimeHostError("public task must expose the entire deployment roster")
        if public_task.scene_id != "room_06" or public_task.task_id != "liv-t-04":
            raise Room06RuntimeHostError("public task identity differs from room06 case")
        task_ids = {task.task_id for task in public_task.graph.tasks}
        # Only this separately assembled public contract crosses the algorithm
        # boundary. Never forward the frozen evaluator package or agent aliases.
        decision = self._decide(public_task, public_task.robots)
        if not isinstance(decision, AlgorithmDecision):
            raise Room06RuntimeHostError("controller decide must return AlgorithmDecision")
        if any(item.task_id not in task_ids or item.robot_id not in public_ids
               for item in decision.assignments):
            raise Room06RuntimeHostError("decision contains unknown task or robot")
        if any(item not in task_ids for item in decision.unassigned_tasks):
            raise Room06RuntimeHostError("decision contains unknown unassigned task")
        pairs = [(item.task_id, item.robot_id) for item in decision.assignments]
        if len(pairs) != len(set(pairs)):
            raise Room06RuntimeHostError("decision contains duplicate assignments")
        if set(decision.unassigned_tasks) & {item.task_id for item in decision.assignments}:
            raise Room06RuntimeHostError("decision task is both assigned and unassigned")
        assignment = self._assignment({
            "tick": self._tick(),
            "assigned_robot_ids": list(dict.fromkeys(item.robot_id for item in decision.assignments)),
            "decision_digest": canonical_digest(asdict(decision)),
        }, instance_agent_map)
        previous = self._tick()
        if assignment["tick"] > previous:
            raise Room06RuntimeHostError("assignment tick is in the future")
        collector.sample_holds()
        step_package = deepcopy(package)
        package_digest = canonical_digest(step_package)
        terminal_reached = False
        for _ in range(self._max_steps):
            if previous == 15000:
                collector.observe_horizon()
                terminal_reached = True
                break
            row = self._step(
                package=step_package, instance_agent_map=dict(instance_agent_map),
                assignment=deepcopy(assignment),
                decision=deepcopy(decision),
            )
            if not isinstance(row, Mapping) or set(row) != {"skill_events"}:
                raise Room06RuntimeHostError("controller step schema mismatch")
            tick = self._tick()
            if tick != previous + 1:
                raise Room06RuntimeHostError("controller step must advance exactly one tick")
            previous = tick
            events = row["skill_events"]
            if not isinstance(events, list):
                raise Room06RuntimeHostError("controller step skill_events must be a list")
            for event in events:
                if not isinstance(event, Mapping) or type(event.get("tick")) is not int or event["tick"] != tick:
                    raise Room06RuntimeHostError("skill event must belong to the current controller tick")
                collector.observe_completed_skill_session(event)
            collector.sample_holds()
            if any(event["event_type"] == "task_accepted" for event in events):
                terminal_reached = True
                break
            if tick == 15000:
                collector.observe_horizon()
                terminal_reached = True
                break
        if not terminal_reached:
            raise Room06RuntimeHostError("controller step loop ended before observed terminal state")
        if canonical_digest(step_package) != package_digest:
            raise Room06RuntimeHostError("controller loop modified its frozen package")
        terminal = self._terminal_inputs(
            package=deepcopy(package), instance_agent_map=dict(instance_agent_map),
            assignment=deepcopy(assignment),
        )
        if not isinstance(terminal, Mapping) or set(terminal) != {
            "episode", "d1", "d4_first_event_observation", "d6_measurement"
        }:
            raise Room06RuntimeHostError("terminal_inputs must contain measured D1/D4/D6 fields")
        collector.close()
        return {"assignment": assignment, "terminal_inputs": dict(terminal)}


def install_room06_runtime(*, base_env: Any, package: Mapping[str, Any],
                           instance_agent_map: Mapping[str, str],
                           clock: Callable[[], int],
                           observe: Callable[..., Any],
                           run_session: Callable[..., Any],
                           admission: Callable[..., bool],
                           monitor_session: Callable[..., Any],
                           run_case: Callable[..., Mapping[str, Any]]) -> None:
    """Install benchmark-owned logical runtime and a real task driver.

    Every callback is mandatory. The function only binds frozen package data;
    it never creates task objects, chooses robots, or treats a command as a
    completed observation.
    """

    if not isinstance(package, Mapping) or not isinstance(instance_agent_map, Mapping):
        raise Room06RuntimeHostError("package and instance_agent_map are required")
    body = dict(package)
    if body.pop("package_digest", None) != canonical_digest(body):
        raise Room06RuntimeHostError("package digest mismatch")
    selected = package.get("plan", {}).get("selected_robot_ids")
    if not isinstance(selected, list) or set(selected) != set(instance_agent_map):
        raise Room06RuntimeHostError("host roster differs from the frozen package")
    agents = tuple(instance_agent_map.values())
    if any(not isinstance(a, str) or not a.strip() for a in agents) or len(set(agents)) != len(agents):
        raise Room06RuntimeHostError("runtime agent bindings must be nonempty and unique")
    callbacks = (clock, observe, run_session, admission, monitor_session, run_case)
    if any(not callable(callback) for callback in callbacks):
        raise Room06RuntimeHostError("all room06 host callbacks are required")
    if getattr(base_env, "logical_object_runtime", None) is not None:
        raise Room06RuntimeHostError("logical object runtime was already registered")
    if getattr(base_env, "room06_task_driver", None) is not None:
        raise Room06RuntimeHostError("room06 task driver was already registered")
    try:
        objects = logical_object_definitions(package["task_setup"])
        runtime = LogicalObjectRuntime(
            scene_id="room_06", objects=objects,
            robot_ids=tuple(instance_agent_map), clock=clock,
            observe=observe, run_session=run_session,
            admission=admission, monitor_session=monitor_session,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Room06RuntimeHostError(str(exc)) from exc
    base_env.logical_object_runtime = runtime
    base_env.room06_task_driver = Room06TaskDriver(run_case=run_case)


def install_controller_backed_room06_runtime(*, base_env: Any, package: Mapping[str, Any],
                                              instance_agent_map: Mapping[str, str],
                                              public_task: EaiTaskPackage,
                                              operation_nodes: Mapping[str, Mapping[str, list[list[float]]]],
                                              root_heights: Mapping[str, float],
                                              decide: DecisionProvider, step: Callable[..., Mapping[str, Any]],
                                              terminal_inputs: Callable[..., Mapping[str, Any]],
                                              clock: Callable[[], int],
                                              admission: Callable[..., bool],
                                              arrival_radius_m: float = 0.35,
                                              max_linear_speed_m_s: float = 0.08,
                                              max_angular_speed_rad_s: float = 0.12) -> None:
    """Bind the real Isaac callbacks in one explicit production setup.

    Missing callbacks are rejected before task collection. ``operation_nodes``
    is derived from the frozen semantic map by the caller; the tested system
    receives only ``public_task``. This helper is intentionally separate from
    the generic CPU fixture installer so a GPU case cannot silently fall back
    to a synthetic run loop.
    """
    from .logical_object_controller import IsaacLogicalArrivalReader
    from .logical_skill_sessions import IsaacLogicalSkillSessions
    driver = ControllerBackedRoom06TaskDriver(
        public_task=public_task, decide=decide, step=step, clock=clock,
        terminal_inputs=terminal_inputs,
    )
    reader = IsaacLogicalArrivalReader(
        base_env=base_env, instance_agent_map=instance_agent_map,
        root_heights=root_heights, operation_nodes=operation_nodes,
        arrival_radius_m=arrival_radius_m,
        max_linear_speed_m_s=max_linear_speed_m_s,
        max_angular_speed_rad_s=max_angular_speed_rad_s,
    )
    sessions = IsaacLogicalSkillSessions(base_env=base_env,
        instance_agent_map=instance_agent_map, arrival_reader=reader)
    install_room06_runtime(
        base_env=base_env, package=package,
        instance_agent_map=instance_agent_map, clock=clock,
        observe=reader, run_session=sessions.run, admission=admission,
        monitor_session=sessions.monitor, run_case=driver.collect_room06,
    )
    base_env.room06_task_driver = driver
    base_env.room06_arrival_reader = reader
    base_env.room06_skill_sessions = sessions


__all__ = [
    "ControllerBackedRoom06TaskDriver",
    "Room06RuntimeHostError",
    "Room06TaskDriver",
    "install_room06_runtime",
    "install_controller_backed_room06_runtime",
]
