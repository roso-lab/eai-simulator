"""Legacy fixed-task planner retained for older EAI adapter commands.

The paper-aligned demo2 integration uses ``TeamWeaverPipeline`` and must not
import this one-shot facade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from TeamWeaver.eai_adapter.dynamic_allocator import (
    AllocationResult,
    DynamicFactoryAllocator,
    RobotState,
)


@dataclass(frozen=True)
class FactoryPlanningResult:
    hazard_id: int
    decomposition_source: str
    allocation: AllocationResult


class FactoryTeamPlanner:
    def __init__(
        self,
        *,
        decomposer: Any | None = None,
        allocator: DynamicFactoryAllocator | None = None,
    ) -> None:
        if decomposer is None:
            raise RuntimeError(
                "Legacy FactoryTeamPlanner requires an explicit legacy decomposer; "
                "demo2 uses TeamWeaverPipeline instead"
            )
        self.decomposer = decomposer
        self.allocator = allocator or DynamicFactoryAllocator()

    def plan(
        self,
        *,
        hazard_id: int,
        robots: Sequence[RobotState],
    ) -> FactoryPlanningResult:
        decomposition = self.decomposer.decompose(hazard_id)
        allocation = self.allocator.allocate(robots, decomposition.tasks)
        return FactoryPlanningResult(
            hazard_id=hazard_id,
            decomposition_source=decomposition.source,
            allocation=allocation,
        )
