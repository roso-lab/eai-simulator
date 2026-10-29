"""Robot action controllers used by the fixed-action PickPlace adapters."""

from .drone_action import DroneActionTemplate
from .pepper_action import PepperActionTemplate

__all__ = ["ActionOnlyManipulationRuntime", "IdealizedManipulationRuntime",
           "DroneActionTemplate", "PepperActionTemplate"]


def __getattr__(name):
    if name in {"ActionOnlyManipulationRuntime", "IdealizedManipulationRuntime"}:
        from .arm_action_runtime import ActionOnlyManipulationRuntime, IdealizedManipulationRuntime
        return {"ActionOnlyManipulationRuntime": ActionOnlyManipulationRuntime,
                "IdealizedManipulationRuntime": IdealizedManipulationRuntime}[name]
    raise AttributeError(name)
