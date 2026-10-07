"""Input-domain audit for the ICRA 2024 DCMRTA checkpoint.

The official policy is trained for 10--20 agents and 20--50 tasks with five
task requirements and coalition formation. Fire Rescue has four heterogeneous
robots and four indivisible skills. Padding Fire Rescue to the native tensor
shape would produce a runnable tensor, but would not be a valid zero-shot
transfer claim. This module therefore exposes the transformation only as an
auditable native-decision descriptor; the physical EAI track uses the declared
default assignment in ``fire_rescue_policy.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DCMRTAInputSpec:
    native_agent_range: tuple[int, int] = (10, 20)
    native_task_range: tuple[int, int] = (20, 50)
    native_trait_dim: int = 1
    native_agent_input_dim: int = 6
    native_task_input_dim: int = 5
    coalition_size: int = 5
    eai_agents: int = 4
    eai_tasks: int = 4
    padded_agents: int = 10
    padded_tasks: int = 20
    decision_status: str = "cross_domain_mismatch"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_native_input_spec(
    robot_positions: Mapping[str, Sequence[float]],
    task_ids: Sequence[str],
) -> DCMRTAInputSpec:
    """Describe, without silently executing, the official padding transform."""
    return DCMRTAInputSpec(
        eai_agents=len(robot_positions),
        eai_tasks=len(task_ids),
        padded_agents=max(10, len(robot_positions)),
        padded_tasks=max(20, len(task_ids)),
    )


__all__ = ["DCMRTAInputSpec", "build_native_input_spec"]
