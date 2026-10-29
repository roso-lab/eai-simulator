"""Action-only arm runtime facade used by PickPlace adapters.

The implementation delegates to EAI's shared IK/controller runtime. This
module is the task-layer ownership boundary: callers may use the fixed-action
entry point, while legacy payload attachment APIs remain outside PickPlace.
"""
from EAI.controllers.idealized_manipulation import IdealizedManipulationRuntime

ActionOnlyManipulationRuntime = IdealizedManipulationRuntime

__all__ = ["ActionOnlyManipulationRuntime", "IdealizedManipulationRuntime"]
