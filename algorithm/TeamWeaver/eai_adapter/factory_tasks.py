"""Legacy Factory task specifications used by the one-shot compatibility API.

The paper-aligned demo2 flow does not use this catalog for decomposition; its
tasks are produced by the mandatory DeepSeek semantic decomposer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


HAZARD_POSITIONS: dict[int, tuple[float, float, float]] = {
    1: (-6.0, -4.0, 0.0),
    2: (-5.0, 2.5, 0.0),
    3: (0.25, 9.5, 0.0),
    4: (3.5, -2.5, 0.0),
    5: (-8.11, 8.66, 0.0),
}

SUBTASK_NAMES = {
    "red": "hazard scout",
    "yellow": "extinguisher run",
    "blue": "data relay",
    "green": "open rescue channel",
}

FIXED_TARGETS = {
    "yellow": ("fire_extinguisher_pickup", (1.77, -9.38)),
    "green": ("rescue_channel_button", (10.58, 1.0)),
}

FACTORY_TASK_CAPABILITY_REQUIREMENTS: Mapping[str, Mapping[str, float]] = {
    "red": {"navigation": 1.0, "sensing": 0.9, "agility": 0.6},
    "yellow": {"navigation": 1.0, "payload": 1.0, "manipulation": 0.7},
    "blue": {"navigation": 1.0, "relay": 1.0, "sensing": 0.6},
    "green": {"navigation": 1.0, "agility": 0.9, "manipulation": 0.8},
}


@dataclass(frozen=True)
class FactoryTaskSpec:
    task_id: str
    name: str
    target_name: str
    target_xy: tuple[float, float]
    capability_requirements: Mapping[str, float]
    hard_capabilities: tuple[str, ...] = ("navigation",)
    priority: int = 1


def build_factory_task_specs(hazard_id: int) -> list[FactoryTaskSpec]:
    if hazard_id not in HAZARD_POSITIONS:
        raise ValueError(f"Unknown factory hazard id: {hazard_id}")
    hazard_position = HAZARD_POSITIONS[hazard_id]
    return [
        FactoryTaskSpec(
            task_id=subtask_id,
            name=SUBTASK_NAMES[subtask_id],
            target_name=target_name,
            target_xy=target_xy,
            capability_requirements=FACTORY_TASK_CAPABILITY_REQUIREMENTS[
                subtask_id
            ],
        )
        for subtask_id in ("red", "yellow", "blue", "green")
        for target_name, target_xy in (
            _target_for_subtask(subtask_id, hazard_id, hazard_position),
        )
    ]


def _target_for_subtask(
    subtask_id: str,
    hazard_id: int,
    hazard_position: tuple[float, float, float],
) -> tuple[str, tuple[float, float]]:
    if subtask_id in FIXED_TARGETS:
        return FIXED_TARGETS[subtask_id]
    hazard_x, hazard_y = hazard_position[:2]
    if subtask_id == "red":
        return f"hazard_{hazard_id}_red_scout", (hazard_x + 1.5, hazard_y)
    if subtask_id == "blue":
        return f"hazard_{hazard_id}_blue_relay", (hazard_x - 1.5, hazard_y)
    raise ValueError(f"Unsupported factory subtask id: {subtask_id}")

