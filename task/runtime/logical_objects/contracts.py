"""Small, simulator-independent contracts for the logical object runtime."""

from dataclasses import dataclass
import hashlib
import json

MODEL = "heterocoord.logical-task-objects.v1"
CHECKPOINT = "heterocoord.logical-task-objects-checkpoint.v1"


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ControllerObservation:
    robot_id: str
    semantic_point: str
    tick: int
    arrived: bool
    evidence_digest: str


@dataclass(frozen=True)
class SkillSessionResult:
    session_id: str
    request_id: str
    robot_ids: tuple[str, ...]
    object_id: str
    operation: str
    semantic_point: str
    tick: int
    status: str
    evidence_digest: str


__all__ = ["MODEL", "CHECKPOINT", "ControllerObservation", "SkillSessionResult", "digest"]
