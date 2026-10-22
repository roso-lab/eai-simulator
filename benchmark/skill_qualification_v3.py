"""Physical-skill qualification records and track separation."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
import math
from typing import Any, Mapping


class SkillTrack(str, Enum):
    PRIMARY_PHYSICAL = "primary_physical"
    IDEALIZED_DIAGNOSTIC = "idealized_follow_proxy_diagnostic"
    LOGICAL_ONLY = "logical_only"


class SkillQualificationStatus(str, Enum):
    QUALIFIED = "qualified"
    PENDING = "pending"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True)
class SkillQualificationV3:
    skill_id: str
    robot_id: str
    controller_id: str
    track: SkillTrack
    status: SkillQualificationStatus
    physics_dt_s: float
    payload_envelope_kg: float | None
    evidence_digests: tuple[str, ...]
    collision_verified: bool
    release_verified: bool
    stop_guard_verified: bool
    reason: str
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "track", SkillTrack(self.track))
        object.__setattr__(self, "status", SkillQualificationStatus(self.status))
        if not self.skill_id.strip() or not self.robot_id.strip() or not self.controller_id.strip():
            raise ValueError("skill, robot and controller IDs are required")
        if not math.isfinite(float(self.physics_dt_s)) or self.physics_dt_s <= 0:
            raise ValueError("physics_dt_s must be finite and positive")
        if self.status is SkillQualificationStatus.QUALIFIED:
            if self.track is not SkillTrack.PRIMARY_PHYSICAL:
                raise ValueError("only primary physical track may be qualified")
            if not (self.collision_verified and self.release_verified and self.stop_guard_verified):
                raise ValueError("qualified skill requires collision, release and stop evidence")
            if not self.evidence_digests:
                raise ValueError("qualified skill requires evidence digests")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["track"] = self.track.value
        value["status"] = self.status.value
        value["evidence_digests"] = list(self.evidence_digests)
        return value


def follow_proxy_diagnostic(*, skill_id: str, robot_id: str, controller_id: str, physics_dt_s: float, reason: str = "follow proxy changes object physics and writes pose") -> SkillQualificationV3:
    return SkillQualificationV3(skill_id, robot_id, controller_id, SkillTrack.IDEALIZED_DIAGNOSTIC, SkillQualificationStatus.UNSUPPORTED, physics_dt_s, None, (), False, False, False, reason, {"primary_eligible": False})


__all__ = ["SkillTrack", "SkillQualificationStatus", "SkillQualificationV3", "follow_proxy_diagnostic"]
