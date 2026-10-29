"""Fixed bilateral down/up action template for Pepper."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PepperActionTemplate:
    lift_m: float = 0.12
    stable_steps: int = 8


__all__ = ["PepperActionTemplate"]
