"""Task-specific evaluator adapters used by the production receipt seam."""

from .room06_livt04 import (
    EVALUATOR_ID,
    EVALUATOR_VERSION,
    DIAGNOSTIC_OBLIGATION_BINDING,
    Room06LivT04Evaluator,
    Room06LivT04EvaluatorError,
    build_diagnostic_runtime_fixture,
)

__all__ = [
    "EVALUATOR_ID",
    "EVALUATOR_VERSION",
    "DIAGNOSTIC_OBLIGATION_BINDING",
    "Room06LivT04Evaluator",
    "Room06LivT04EvaluatorError",
    "build_diagnostic_runtime_fixture",
]
