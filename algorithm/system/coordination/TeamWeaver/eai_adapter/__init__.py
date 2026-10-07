"""Public EAI integration API for the TeamWeaver planning core."""

from .capability_ontology import (
    CAPABILITIES,
    PlanValidationError,
    TaskValidationError,
    validate_plan_payload,
)
from .capability_tracker import (
    CapabilityTracker,
    CapabilityUpdate,
)
from .dynamic_allocator import (
    AllocationResult,
    RobotState,
)
from .dynamic_miqp import (
    AllocationResult as PhaseAllocationResult,
    DynamicMIQPAllocator,
    ObjectiveBreakdown,
    PhaseAssignment,
)
from .mission_graph import (
    FIRE_RETURN_RADIUS_M,
    append_fire_return_tasks,
)
from .phase_scheduler import PhaseScheduler
from .scenario_config import (
    TeamWeaverScenarioConfig,
    get_active_scenario,
    set_active_scenario,
)
from .semantic_decomposer import (
    DecompositionCancelled,
    DecompositionError,
    DeepSeekSemanticDecomposer,
    SemanticDecompositionResult,
)
from .task_models import (
    ExecutionFeedback,
    ObstacleSnapshot,
    SemanticTask,
    SymbolicWorldState,
)
from .teamweaver_pipeline import (
    TeamWeaverPipeline,
    TeamWeaverPlan,
)
from .benchmark_condition import (
    ALGORITHM_ID as BENCHMARK_ALGORITHM_ID,
    assign as fire_rescue_assign,
    condition_manifest,
    condition_record,
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
    "BENCHMARK_ALGORITHM_ID",
    "condition_manifest",
    "condition_record",
    "fire_rescue_assign",
]
