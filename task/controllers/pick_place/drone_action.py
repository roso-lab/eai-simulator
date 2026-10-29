"""Fixed vertical action template for aerial PickPlace adapters."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DroneActionTemplate:
    """Distances used after navigation has brought the drone to the work area."""

    clearance_m: float = 0.30
    align_xy_tolerance_m: float = 0.08
    align_z_tolerance_m: float = 0.05


__all__ = ["DroneActionTemplate"]
