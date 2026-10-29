"""Deterministic allocation adapters for bar logic runs."""
from benchmark.hetero_diagnostic_runtime_v1 import GreedyGoalAlgorithm
class EmosBarAlgorithm(GreedyGoalAlgorithm):
 algorithm_id="EMOS-bar-case-goal-v1"
class TeamWeaverBarAlgorithm(GreedyGoalAlgorithm):
 algorithm_id="TeamWeaver-bar-allocation-v1"
__all__=["EmosBarAlgorithm","TeamWeaverBarAlgorithm"]
