"""Dependency-free rigid poses in metres, with wxyz quaternions."""
from dataclasses import dataclass
import math
from ..contracts import SkillContractError


def number(value, name="number"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SkillContractError(f"{name} must be finite numeric")
    return float(value)


def vector(value, size=3):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise SkillContractError(f"expected {size} numeric coordinates")
    return tuple(number(x) for x in value)


def conjugate(q):
    return q[0], -q[1], -q[2], -q[3]


def rotate(q, v):
    w, x, y, z = q
    tx, ty, tz = 2*(y*v[2]-z*v[1]), 2*(z*v[0]-x*v[2]), 2*(x*v[1]-y*v[0])
    return (v[0]+w*tx+y*tz-z*ty, v[1]+w*ty+z*tx-x*tz, v[2]+w*tz+x*ty-y*tx)


@dataclass(frozen=True)
class RigidPose:
    position_m: tuple[float, float, float]
    orientation_wxyz: tuple[float, float, float, float] = (1., 0., 0., 0.)

    def __post_init__(self):
        object.__setattr__(self, "position_m", vector(self.position_m))
        q = vector(self.orientation_wxyz, 4)
        norm = math.hypot(*q)
        if not math.isfinite(norm):
            raise SkillContractError("quaternion norm must be finite")
        if norm < 1e-12:
            raise SkillContractError("quaternion must be nonzero")
        object.__setattr__(self, "orientation_wxyz", tuple(x/norm for x in q))

    def to_world(self, local):
        offset = rotate(self.orientation_wxyz, vector(local))
        return tuple(self.position_m[i]+offset[i] for i in range(3))

    def to_local(self, world):
        world = vector(world)
        return rotate(conjugate(self.orientation_wxyz), tuple(world[i]-self.position_m[i] for i in range(3)))
