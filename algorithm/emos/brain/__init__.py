"""
EAI Brain - LLM-based Multi-Robot Coordination System.

Heavy submodules (policy, Isaac agents, skills) are loaded lazily so that
``from algorithm.emos.brain.MultiLLM_discussion import group_discussion`` (with
``EAI`` workspace root on ``sys.path``) does not import Isaac Lab / EAI_assets. EMOS only needs the discussion + resume
modules at runtime.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, Tuple

# Names exported for `from algorithm.emos.brain import X`
_LAZY_EXPORTS: Dict[str, Tuple[str, str]] = {
    # multi_llm_policy
    "MultiLLMPolicy": (".multi_llm_policy", "MultiLLMPolicy"),
    "PolicyConfig": (".multi_llm_policy", "PolicyConfig"),
    "LLMMultiRobotEnvWrapper": (".multi_llm_policy", "LLMMultiRobotEnvWrapper"),
    "ABLATION_MODE": (".multi_llm_policy", "ABLATION_MODE"),
    # llm_agent
    "IsaacLLMAgent": (".llm_agent", "IsaacLLMAgent"),
    "AgentArguments": (".llm_agent", "AgentArguments"),
    # robot_resume
    "generate_robot_resume": (".robot_resume", "generate_robot_resume"),
    "generate_all_robot_resumes": (".robot_resume", "generate_all_robot_resumes"),
    "infer_robot_type": (".robot_resume", "infer_robot_type"),
    "get_capabilities_text": (".robot_resume", "get_capabilities_text"),
    # discussion
    "group_discussion": (".MultiLLM_discussion", "group_discussion"),
    # actions
    "ACTION_POOL": (".actions.robot_actions", "ACTION_POOL"),
    # skills
    "BaseSkill": (".skills", "BaseSkill"),
    "SkillStatus": (".skills", "SkillStatus"),
    "SkillResult": (".skills", "SkillResult"),
    "NavigationSkill": (".skills", "NavigationSkill"),
    "CoordinationSkill": (".skills", "CoordinationSkill"),
}

__all__ = list(_LAZY_EXPORTS.keys())


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        mod_path, attr = _LAZY_EXPORTS[name]
        mod = importlib.import_module(mod_path, __name__)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals().keys()))
