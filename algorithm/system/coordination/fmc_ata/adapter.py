"""Small, deterministic FMC-ATA market/message boundary for EAI.

FMC-ATA extends Fisher-market task allocation with asynchronous messages,
latency and loss.  EAI tasks normally represent indivisible physical skills,
so this adapter requires an explicit ``demand``/fraction interpretation and
returns fractional allocations for the fleet service to translate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from algorithm.common_coordination import ContractError, CoordinationTask, RobotCapability


@dataclass(frozen=True)
class FMCMessage:
    sender: str
    recipient: str
    kind: str
    payload: Mapping[str, object]
    sent_at: int
    deliver_at: int


@dataclass(frozen=True)
class FMCAllocation:
    robot_id: str
    task_id: str
    fraction: float
    price: float
    plan_version: str


class CommunicationModel:
    """Deterministic FIFO delay/loss model mirroring the source protocols."""

    def __init__(self, *, delay_steps: int = 0, drop_recipients: Iterable[str] = ()) -> None:
        if isinstance(delay_steps, bool) or int(delay_steps) < 0:
            raise ContractError("delay_steps must be a non-negative integer")
        self.delay_steps = int(delay_steps)
        self.drop_recipients = frozenset(str(item) for item in drop_recipients)
        self.time = 0
        self._pending: list[FMCMessage] = []
        self.delivered: list[FMCMessage] = []
        self.dropped: list[FMCMessage] = []

    def send(self, sender: str, recipient: str, kind: str, payload: Mapping[str, object]) -> FMCMessage:
        message = FMCMessage(sender, recipient, kind, dict(payload), self.time, self.time + self.delay_steps)
        if recipient in self.drop_recipients:
            self.dropped.append(message)
        else:
            self._pending.append(message)
        return message

    def tick(self) -> tuple[FMCMessage, ...]:
        self.time += 1
        ready = tuple(message for message in self._pending if message.deliver_at <= self.time)
        self._pending = [message for message in self._pending if message.deliver_at > self.time]
        self.delivered.extend(ready)
        return ready


class FMCATAPlanner:
    """Fisher-style proportional market clearing with asynchronous triggers."""

    def __init__(self, robots: Sequence[RobotCapability], *, communication: CommunicationModel | None = None) -> None:
        self.robots = tuple(robot for robot in robots if robot.active)
        if not self.robots:
            raise ContractError("FMC-ATA requires at least one active robot")
        self.communication = communication or CommunicationModel()
        self.prices: dict[str, float] = {}
        self.round = 0
        self.events: list[dict[str, object]] = []

    def allocate(
        self,
        tasks: Sequence[CoordinationTask],
        *,
        utilities: Mapping[tuple[str, str], float],
        demand: Mapping[str, float] | None = None,
        max_iterations: int = 32,
    ) -> tuple[FMCAllocation, ...]:
        if not tasks:
            return ()
        demand = {task.task_id: float((demand or {}).get(task.task_id, 1.0)) for task in tasks}
        if any(value <= 0 for value in demand.values()):
            raise ContractError("task demand must be positive")
        task_by_id = {task.task_id: task for task in tasks}
        self.prices = {task.task_id: max(1e-6, self.prices.get(task.task_id, 1.0)) for task in tasks}
        # Each buyer has one unit of budget.  Over-demanded goods increase in
        # price; buyers reselect after messages arrive, which is the essential
        # asynchronous FMC-ATA boundary.
        allocation: dict[tuple[str, str], float] = {}
        for iteration in range(max_iterations):
            allocation.clear()
            choices: dict[str, str] = {}
            for robot in self.robots:
                candidates = [
                    task_id
                    for task_id, task in task_by_id.items()
                    if task.required_capabilities <= robot.capabilities
                    and float(utilities.get((robot.robot_id, task_id), 0.0)) > 0
                ]
                if not candidates:
                    continue
                selected = max(
                    candidates,
                    key=lambda task_id: (
                        float(utilities.get((robot.robot_id, task_id), 0.0)) / self.prices[task_id],
                        -self.prices[task_id],
                        task_id,
                    ),
                )
                choices[robot.robot_id] = selected
                allocation[(robot.robot_id, selected)] = allocation.get((robot.robot_id, selected), 0.0) + 1.0
                for recipient in sorted(self._robot_ids()):
                    self.communication.send(robot.robot_id, recipient, "bid", {"task_id": selected, "utility": utilities.get((robot.robot_id, selected), 0.0)})
            overloaded = [task_id for task_id in task_by_id if sum(value for (robot_id, tid), value in allocation.items() if tid == task_id) > demand[task_id]]
            self.events.append({"kind": "market_round", "iteration": iteration, "choices": dict(choices), "overloaded": tuple(sorted(overloaded))})
            if not overloaded:
                break
            for task_id in overloaded:
                self.prices[task_id] *= 1.0 + 1.0 / (1.0 + iteration)
            self.communication.tick()
        self.round += 1
        allocations: list[FMCAllocation] = []
        totals = {
            task_id: sum(value for (robot_id, current_task), value in allocation.items() if current_task == task_id)
            for task_id in task_by_id
        }
        for (robot_id, task_id), units in sorted(allocation.items()):
            if units <= 0:
                continue
            # Convert unit budgets to a divisible task share.  This keeps the
            # sum of robot fractions bounded by the requested demand even when
            # delayed messages leave the market temporarily over-demanded.
            total_units = max(totals[task_id], 1e-9)
            fraction = min(1.0, total_units / max(demand[task_id], 1e-9)) * (units / total_units)
            allocations.append(FMCAllocation(robot_id, task_id, fraction, self.prices[task_id], f"fmc-ata-r{self.round}"))
        return tuple(allocations)

    def _robot_ids(self) -> tuple[str, ...]:
        return tuple(robot.robot_id for robot in self.robots)

    def notify_arrival(self, task: CoordinationTask) -> None:
        self.events.append({"kind": "task_arrival", "task_id": task.task_id, "time": self.communication.time})
