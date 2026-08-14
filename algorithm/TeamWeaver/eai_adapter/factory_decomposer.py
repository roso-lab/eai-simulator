"""Compatibility imports for the dynamic EAI semantic decomposer.

The former implementation required fixed red/yellow/blue/green task IDs. That
contract is intentionally retired; demo2 must provide an instruction and a
symbolic world snapshot to the dynamic decomposer.
"""

from TeamWeaver.eai_adapter.semantic_decomposer import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DecompositionAttemptError,
    DecompositionError,
    DeepSeekSemanticDecomposer,
    SemanticDecompositionResult,
)


FactoryTaskDecomposer = DeepSeekSemanticDecomposer
FactoryDecompositionResult = SemanticDecompositionResult
DEFAULT_DEEPSEEK_BASE_URL = DEFAULT_BASE_URL
DEFAULT_DEEPSEEK_MODEL = DEFAULT_MODEL

__all__ = [
    "DeepSeekSemanticDecomposer",
    "DecompositionAttemptError",
    "DecompositionError",
    "SemanticDecompositionResult",
    "FactoryTaskDecomposer",
    "FactoryDecompositionResult",
    "DEFAULT_DEEPSEEK_BASE_URL",
    "DEFAULT_DEEPSEEK_MODEL",
]
