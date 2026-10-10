"""Pure state machine for the idealized Unitree G1 box-carry skill.

The skill intentionally models a symbolic/kinematic grasp.  The hands move to
the two sides of a box, the box is logically welded to the pair of hand
targets, and the box follows the carry/place targets without relying on
contact friction.  Isaac-specific IK and scene access live in ``controller``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import os
from typing import Any


Vec3 = tuple[float, float, float]
DEFAULT_BOX_SIZE: Vec3 = (0.26, 0.28, 0.30)


def parse_box_size(value: str) -> Vec3:
    """Parse a positive ``X,Y,Z`` box-size override in metres."""

    parts = tuple(part.strip() for part in str(value).split(","))
    if len(parts) != 3:
        raise ValueError("box size must be three comma-separated values: X,Y,Z")
    try:
        result = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise ValueError("box size must contain numeric values: X,Y,Z") from exc
    if any(item <= 0.0 for item in result):
        raise ValueError("box size values must be positive")
    return result  # type: ignore[return-value]


def box_size_override_from_environment() -> Vec3 | None:
    """Return the process-scoped validation override, if one is configured."""

    raw = os.environ.get("EAI_BOX_GRASP_SIZE", "").strip()
    return parse_box_size(raw) if raw else None


class G1BoxGraspPhase(str, Enum):
    IDLE = "idle"
    APPROACH = "approach"
    GRASP = "grasp"
    CARRY = "carry"
    PLACE = "place"
    RELEASE = "release"
    RETURN = "return"
    DONE = "done"


@dataclass(frozen=True)
class G1BoxGraspConfig:
    """Geometry and timing for one idealized front-facing box transfer.

    Coordinates are world metres.  The default G1 spawn is at the origin and
    faces +X, so the default box is directly in front of the chest.  ``box``
    is a (length-X, width-Y, height-Z) cuboid; the side grasp points are just
    outside its +/-Y faces.
    """

    # Calibrated against the bundled G1 USD: the palms can reach roughly
    # x=0.20 m from this fixed root without moving the base.  Keeping the
    # carton in that workspace is what makes the visual grasp physically
    # believable instead of relying on a phase-only teleport.
    box_position: Vec3 = (0.20, 0.0, 0.62)
    # The side-to-side span is deliberately compact so both arms can reach
    # the carton from a stationary, front-facing robot.  Dimensions are
    # (front-back X, left-right Y, height Z) in metres.
    box_size: Vec3 = DEFAULT_BOX_SIZE
    place_position: Vec3 = (0.20, 0.0, 0.15)
    # A conservative carry point just in front of the lower chest keeps both
    # palms inside the calibrated G1 workspace while still visibly lifting the
    # carton off the pickup height.
    # The pickup pose is already in front of the lower chest for both bundled
    # G1 and Pepper assets.  Keep the carry point close to it so the two
    # real arm chains stay wrapped around the carton instead of asking IK for
    # an unreachable high lift.
    chest_position: Vec3 = (0.19, 0.0, 0.63)
    side_clearance_m: float = 0.045
    # Some robot assets cannot bring the two palm links closer than a small
    # comfortable lateral span without entering a singular configuration.
    # A robot-specific controller may raise this floor; the distance gate still
    # measures the palms against the resulting side targets in world metres.
    min_side_offset_m: float = 0.0
    release_spread_m: float = 0.18
    approach_duration_s: float = 2.0
    grasp_duration_s: float = 0.45
    carry_duration_s: float = 1.5
    place_duration_s: float = 2.0
    release_duration_s: float = 0.45
    return_duration_s: float = 1.8
    # M20-style physical reach gate.  The idealized weld is only enabled once
    # both palm links are actually within this distance of their side targets.
    grasp_distance_threshold_m: float = 0.10
    # Smaller cartons can put the G1 elbow near a slower Jacobian direction;
    # give the physical distance gate enough time to converge while keeping
    # attachment forbidden until both palms are actually inside the threshold.
    grasp_timeout_s: float = 10.0
    cube_prim_name: str = "g1_grasp_box"

    def __post_init__(self) -> None:
        for name in ("box_position", "box_size", "place_position", "chest_position"):
            value = getattr(self, name)
            if len(value) != 3 or not all(isinstance(item, (int, float)) for item in value):
                raise ValueError(f"{name} must contain three numeric coordinates")
        if any(float(item) <= 0.0 for item in self.box_size):
            raise ValueError("box_size values must be positive")
        for name in (
            "side_clearance_m",
            "min_side_offset_m",
            "release_spread_m",
            "approach_duration_s",
            "grasp_duration_s",
            "carry_duration_s",
            "place_duration_s",
            "release_duration_s",
            "return_duration_s",
            "grasp_distance_threshold_m",
            "grasp_timeout_s",
        ):
            if float(getattr(self, name)) < 0.0:
                raise ValueError(f"{name} must be non-negative")
        if not self.cube_prim_name.strip():
            raise ValueError("cube_prim_name must not be empty")

    @classmethod
    def from_environment(cls, base: "G1BoxGraspConfig | None" = None) -> "G1BoxGraspConfig":
        """Apply ``EAI_BOX_GRASP_SIZE`` to a base configuration when present.

        The override is intentionally process-scoped so saved environments
        retain their nominal geometry while the validation probe can exercise
        several real box sizes without duplicating scene/controller code.
        """

        config = base or cls()
        size = box_size_override_from_environment()
        return replace(config, box_size=size) if size is not None else config

    @property
    def side_offset(self) -> float:
        geometric_offset = float(self.box_size[1]) * 0.5 + float(self.side_clearance_m)
        return max(geometric_offset, float(self.min_side_offset_m))

    def side_targets(self, center: Vec3 | None = None) -> dict[str, Vec3]:
        """Return left/right palm targets around ``center``.

        The G1 is assumed to face +X and uses +Y for its left-hand side.
        """

        x, y, z = center if center is not None else self.box_position
        offset = self.side_offset
        return {
            "left": (float(x), float(y) + offset, float(z)),
            "right": (float(x), float(y) - offset, float(z)),
        }

    def release_targets(self) -> dict[str, Vec3]:
        x, y, z = self.place_position
        offset = self.side_offset + float(self.release_spread_m)
        return {
            "left": (float(x), float(y) + offset, float(z) + 0.08),
            "right": (float(x), float(y) - offset, float(z) + 0.08),
        }

    def home_targets(self) -> None:
        """Marker used by the runtime to select the authored G1 home pose."""

        return None


class G1BoxGraspState:
    """Deterministic phase/timing state for the G1 box skill."""

    _DURATIONS = {
        G1BoxGraspPhase.APPROACH: "approach_duration_s",
        G1BoxGraspPhase.GRASP: "grasp_duration_s",
        G1BoxGraspPhase.CARRY: "carry_duration_s",
        G1BoxGraspPhase.PLACE: "place_duration_s",
        G1BoxGraspPhase.RELEASE: "release_duration_s",
        G1BoxGraspPhase.RETURN: "return_duration_s",
    }

    def __init__(self, config: G1BoxGraspConfig | None = None) -> None:
        self.config = config or G1BoxGraspConfig()
        self.phase = G1BoxGraspPhase.IDLE
        self.phase_elapsed_s = 0.0
        self.attached = False
        self.grasp_ready = True
        self.completed_cycles = 0

    @property
    def active(self) -> bool:
        return self.phase not in {G1BoxGraspPhase.IDLE, G1BoxGraspPhase.DONE}

    def start(self) -> None:
        if self.active:
            raise RuntimeError(f"cannot start while phase is {self.phase.value}")
        self.phase = G1BoxGraspPhase.APPROACH
        self.phase_elapsed_s = 0.0
        self.attached = False
        self.grasp_ready = True

    def reset(self) -> None:
        self.phase = G1BoxGraspPhase.IDLE
        self.phase_elapsed_s = 0.0
        self.attached = False
        self.grasp_ready = True

    def advance(self, dt_s: float) -> G1BoxGraspPhase:
        """Advance the state by ``dt_s`` and return the current phase.

        Zero-duration phases are traversed in one call, which makes the class
        convenient for deterministic tests and scripted skill composition.
        """

        if dt_s < 0.0:
            raise ValueError("dt_s must be non-negative")
        if self.phase in {G1BoxGraspPhase.IDLE, G1BoxGraspPhase.DONE}:
            return self.phase
        remaining = float(dt_s)
        while self.active:
            duration = float(getattr(self.config, self._DURATIONS[self.phase]))
            if self.phase_elapsed_s + remaining < duration:
                self.phase_elapsed_s += remaining
                remaining = 0.0
                break
            remaining = max(0.0, remaining - (duration - self.phase_elapsed_s))
            self.phase_elapsed_s = 0.0
            if self.phase is G1BoxGraspPhase.APPROACH:
                self.phase = G1BoxGraspPhase.GRASP
            elif self.phase is G1BoxGraspPhase.GRASP:
                # A runtime adapter may hold this transition until both end
                # effectors are demonstrably at the carton sides.
                if not self.grasp_ready:
                    self.phase_elapsed_s = duration
                    break
                self.attached = True
                self.phase = G1BoxGraspPhase.CARRY
            elif self.phase is G1BoxGraspPhase.CARRY:
                self.phase = G1BoxGraspPhase.PLACE
            elif self.phase is G1BoxGraspPhase.PLACE:
                self.attached = False
                self.phase = G1BoxGraspPhase.RELEASE
            elif self.phase is G1BoxGraspPhase.RELEASE:
                self.attached = False
                self.phase = G1BoxGraspPhase.RETURN
            elif self.phase is G1BoxGraspPhase.RETURN:
                self.phase = G1BoxGraspPhase.DONE
                self.completed_cycles += 1
            if remaining <= 0.0:
                break
        return self.phase

    def hand_targets(self) -> dict[str, Vec3] | None:
        if self.phase in {G1BoxGraspPhase.APPROACH, G1BoxGraspPhase.GRASP}:
            return self.config.side_targets(self.config.box_position)
        if self.phase is G1BoxGraspPhase.CARRY:
            return self.config.side_targets(self.config.chest_position)
        if self.phase is G1BoxGraspPhase.PLACE:
            return self.config.side_targets(self._place_interpolation())
        if self.phase is G1BoxGraspPhase.RELEASE:
            return self.config.release_targets()
        return None

    def cube_position(self) -> Vec3:
        if self.phase is G1BoxGraspPhase.CARRY:
            return self.config.chest_position
        if self.phase is G1BoxGraspPhase.PLACE:
            return self._place_interpolation()
        if self.phase in {G1BoxGraspPhase.RELEASE, G1BoxGraspPhase.RETURN, G1BoxGraspPhase.DONE}:
            return self.config.place_position
        return self.config.box_position

    def _place_interpolation(self) -> Vec3:
        duration = max(float(self.config.place_duration_s), 1e-9)
        alpha = min(1.0, max(0.0, float(self.phase_elapsed_s) / duration))
        return tuple(
            float(start) + alpha * (float(end) - float(start))
            for start, end in zip(self.config.chest_position, self.config.place_position)
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "phase_elapsed_s": round(float(self.phase_elapsed_s), 6),
            "attached": bool(self.attached),
            "grasp_ready": bool(self.grasp_ready),
            "completed_cycles": int(self.completed_cycles),
            "box_position": list(self.config.box_position),
            "place_position": list(self.config.place_position),
        }


__all__ = [
    "DEFAULT_BOX_SIZE",
    "G1BoxGraspConfig",
    "G1BoxGraspPhase",
    "G1BoxGraspState",
    "box_size_override_from_environment",
    "parse_box_size",
]
