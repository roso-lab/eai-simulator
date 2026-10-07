"""Input-domain audit for the IEEE RA-L 2025 HeteroMRTA checkpoint.

The public implementation trains on 3--5 robot species (three robots per
species), 15--50 tasks and five-dimensional traits/requirements.  Fire Rescue
has four robots and four indivisible skills.  This descriptor makes the native
checkpoint boundary explicit; it does not silently claim zero-shot transfer
after padding the EAI scene.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class HeteroMRTAInputSpec:
    native_species_range: tuple[int, int] = (3, 5)
    native_agents_per_species: int = 3
    native_task_range: tuple[int, int] = (15, 50)
    native_trait_dim: int = 5
    coalition_and_scheduling: bool = True
    eai_agents: int = 4
    eai_tasks: int = 4
    padded_agents: int = 9
    padded_tasks: int = 15
    decision_status: str = "cross_domain_mismatch"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_native_input_spec(
    robot_positions: Mapping[str, Sequence[float]],
    task_ids: Sequence[str],
) -> HeteroMRTAInputSpec:
    """Describe the official input shape without executing a padded policy."""
    return HeteroMRTAInputSpec(
        eai_agents=len(robot_positions),
        eai_tasks=len(task_ids),
        padded_agents=max(9, len(robot_positions)),
        padded_tasks=max(15, len(task_ids)),
    )


__all__ = ["HeteroMRTAInputSpec", "build_native_input_spec"]
