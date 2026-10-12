"""Continuous, monotonic trapezoidal (or triangular) rest-to-rest Z profile."""
from dataclasses import dataclass
import math
from .geometry import number
from ..contracts import SkillContractError


@dataclass(frozen=True)
class VerticalProfile:
    start_z: float
    target_z: float
    speed_m_s: float = 0.1
    acceleration_m_s2: float = 0.5

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, number(getattr(self, name), name))
        if self.speed_m_s <= 0 or self.acceleration_m_s2 <= 0:
            raise SkillContractError("profile speed and acceleration must be positive")

    @property
    def peak_speed(self):
        return min(self.speed_m_s, math.sqrt(abs(self.target_z-self.start_z)*self.acceleration_m_s2))

    @property
    def ramp_s(self):
        return self.peak_speed/self.acceleration_m_s2

    @property
    def cruise_s(self):
        return max(0., abs(self.target_z-self.start_z)/self.peak_speed-self.ramp_s) if self.peak_speed else 0.

    @property
    def duration_s(self):
        return 2*self.ramp_s+self.cruise_s

    def sample(self, elapsed_s):
        """Return (z, velocity); acceleration is bounded on every open segment."""
        t = max(0., min(number(elapsed_s, "elapsed_s"), self.duration_s))
        a, v, ramp = self.acceleration_m_s2, self.peak_speed, self.ramp_s
        direction = 1. if self.target_z >= self.start_z else -1.
        if t >= self.duration_s:
            return self.target_z, 0.
        if t < ramp:
            distance, velocity = .5*a*t*t, a*t
        elif t < ramp+self.cruise_s:
            distance, velocity = .5*v*ramp+v*(t-ramp), v
        else:
            remaining = self.duration_s-t
            distance, velocity = abs(self.target_z-self.start_z)-.5*a*remaining*remaining, a*remaining
        return self.start_z+direction*distance, direction*velocity
