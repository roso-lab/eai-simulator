"""Unified robot resume: env-based (Isaac) or static ``scenario.robot_profiles``."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from .types import EMOSScenarioConfig, RobotProfileSpec


def build_robot_resumes(
    scenario: EMOSScenarioConfig,
    agents: list[str],
    robot_positions: Dict[str, Tuple[float, float]],
    *,
    env: Any = None,
    use_env_resume: bool = True,
    generate_robot_resume_fn: Optional[Callable[[Any, str, bool], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    resumes: Dict[str, Any] = {}
    gen = generate_robot_resume_fn

    for name in agents:
        if use_env_resume and gen is not None and env is not None:
            try:
                resumes[name] = gen(env, name, True)
                continue
            except Exception:
                pass

        profile: Optional[RobotProfileSpec] = scenario.robot_profiles.get(name)
        pos = robot_positions.get(name, (0.0, 0.0))
        if profile:
            resumes[name] = {
                "robot_name": name,
                "robot_type": profile.robot_type,
                "position": list(pos),
                "capabilities": list(profile.capabilities),
            }
        else:
            resumes[name] = {
                "robot_name": name,
                "robot_type": name,
                "position": list(pos),
                "capabilities": [],
            }
    return resumes
