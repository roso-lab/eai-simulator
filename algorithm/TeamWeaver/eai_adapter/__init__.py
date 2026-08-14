"""Public EAI integration API for the TeamWeaver planning core."""

from TeamWeaver.eai_adapter.capability_ontology import (
    CAPABILITIES,
    PlanValidationError,
    TaskValidationError,
    validate_plan_payload,
)
from TeamWeaver.eai_adapter.capability_tracker import (
    CapabilityTracker,
    CapabilityUpdate,
)
from TeamWeaver.eai_adapter.dynamic_allocator import (
    AllocationResult,
    RobotState,
)
from TeamWeaver.eai_adapter.dynamic_miqp import (
    AllocationResult as PhaseAllocationResult,
    DynamicMIQPAllocator,
    ObjectiveBreakdown,
    PhaseAssignment,
)
from TeamWeaver.eai_adapter.mission_graph import (
    FIRE_RETURN_RADIUS_M,
    append_fire_return_tasks,
)
from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler
from TeamWeaver.eai_adapter.scenario_config import (
    TeamWeaverScenarioConfig,
    get_active_scenario,
    set_active_scenario,
)
from TeamWeaver.eai_adapter.semantic_decomposer import (
    DecompositionCancelled,
    DecompositionError,
    DeepSeekSemanticDecomposer,
    SemanticDecompositionResult,
)
from TeamWeaver.eai_adapter.task_models import (
    ExecutionFeedback,
    ObstacleSnapshot,
    SemanticTask,
    SymbolicWorldState,
)
from TeamWeaver.eai_adapter.teamweaver_pipeline import (
    TeamWeaverPipeline,
    TeamWeaverPlan,
)


__all__ = [
    "AllocationResult",
    "CAPABILITIES",
    "CapabilityTracker",
    "CapabilityUpdate",
    "DecompositionCancelled",
    "DecompositionError",
    "DeepSeekSemanticDecomposer",
    "DynamicMIQPAllocator",
    "ExecutionFeedback",
    "FIRE_RETURN_RADIUS_M",
    "ObjectiveBreakdown",
    "ObstacleSnapshot",
    "PhaseAllocationResult",
    "PhaseAssignment",
    "PhaseScheduler",
    "PlanValidationError",
    "RobotState",
    "SemanticDecompositionResult",
    "SemanticTask",
    "SymbolicWorldState",
    "TaskValidationError",
    "TeamWeaverPipeline",
    "TeamWeaverPlan",
    "TeamWeaverScenarioConfig",
    "append_fire_return_tasks",
    "get_active_scenario",
    "set_active_scenario",
    "validate_plan_payload",
]
