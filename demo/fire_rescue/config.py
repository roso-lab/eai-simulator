from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .algorithm_paths import default_factory_map_yaml


@dataclass(frozen=True)
class FireRescueConfig:
    map_yaml: Path = field(default_factory=default_factory_map_yaml)
    waypoint_step: float = 1.0
    prefer_astar: bool = True
    emos_llm_preset: str = "zhipu-glm4-flash"
    trials: int = 4
    trial_hazard_ids: str = "1,2,3,4"
    auto_fire_delay: float = 5.0
    headless: bool = False
    real_time: bool = False

RESCUE_CHANNEL_BUTTON_XY = (10.58, 1.0)
EXTINGUISHER_PICKUP_XY = (1.77, -9.38)

ROBOT_SPAWN_POSES = {
    "carter_1": ((-7.6, -8.0, 0.0), (1.0, 0.0, 0.0, 0.0)),
    "m20_1": ((-3.0, 5.0, 0.52), (1.0, 0.0, 0.0, 0.0)),
    "m20_2": ((3.0, 1.0, 0.52), (1.0, 0.0, 0.0, 0.0)),
    "scout_1": ((6.0, 5.5, 0.2), (1.0, 0.0, 0.0, 0.0)),
}
