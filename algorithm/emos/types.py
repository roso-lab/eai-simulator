"""EMOS scenario configuration types (task background, subtasks, robots, constraints)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
import math
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Tuple


class EMOSPhase(Enum):
    IDLE = auto()
    RUNNING = auto()
    DONE = auto()
    ERROR = auto()


@dataclass
class SubtaskSpec:
    """One assignable subtask (id + keywords for LLM output matching)."""

    id: str
    name: str
    description: str
    match_keywords: List[str] = field(default_factory=list)


@dataclass
class RobotProfileSpec:
    """Static capability profile when env-based resume is not used."""

    robot_type: str
    capabilities: List[str] = field(default_factory=list)


_POSITION_RULE_KINDS = frozenset({"fixed", "anchor_offset", "none"})


@dataclass
class PositionRule:
    """How to compute (x, y) for a subtask when an anchor event fires.

    ``kind="none"`` (or legacy ``fixed`` with ``xy=None``) marks a targetless
    subtask: it has no spatial waypoint and must not invent ``(0, 0)`` or any
    other fabricated coordinate in ``subtask_positions``.
    """

    kind: Literal["fixed", "anchor_offset", "none"]
    xy: Optional[Tuple[float, float]] = None
    dx: float = 0.0
    dy: float = 0.0


def validate_position_rule(rule: PositionRule, *, subtask_id: str = "") -> None:
    """Reject unknown kinds at runtime (not only at scenario_from_dict)."""
    kind = getattr(rule, "kind", None)
    if kind not in _POSITION_RULE_KINDS:
        where = f" for {subtask_id!r}" if subtask_id else ""
        raise ValueError(f"unknown position rule kind {kind!r}{where}")


def is_targetless_position_rule(rule: PositionRule) -> bool:
    """Return True when the rule declares no spatial target."""
    validate_position_rule(rule)
    return rule.kind == "none" or (rule.kind == "fixed" and rule.xy is None)


def resolve_subtask_target_xy(
    scenario: "EMOSScenarioConfig",
    subtask_id: str,
    positions: Mapping[str, Tuple[float, float]],
) -> Optional[Tuple[float, float]]:
    """Resolve one subtask's travel target without inventing coordinates.

    Targetless rules (``none`` / legacy ``fixed`` + ``xy=None``) yield ``None``.
    Ordinary spatial subtasks must already appear in ``positions``; missing
    coordinates and unknown rule kinds raise ``ValueError`` instead of
    defaulting to ``(0, 0)`` or dropping the task.
    """
    if subtask_id not in scenario.subtasks:
        raise ValueError(f"unknown subtask {subtask_id!r}")
    rule = scenario.position_rules.get(subtask_id)
    if rule is not None:
        validate_position_rule(rule, subtask_id=subtask_id)
        if is_targetless_position_rule(rule):
            return None
    if subtask_id not in positions:
        raise ValueError(f"missing spatial target for subtask {subtask_id!r}")
    xy = positions[subtask_id]
    if (
        not isinstance(xy, (tuple, list))
        or len(xy) != 2
        or not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in xy)
    ):
        raise ValueError(f"invalid spatial target for subtask {subtask_id!r}")
    gx, gy = float(xy[0]), float(xy[1])
    if not (math.isfinite(gx) and math.isfinite(gy)):
        raise ValueError(f"invalid spatial target for subtask {subtask_id!r}")
    return (gx, gy)


def open_subtask_ids(
    scenario: "EMOSScenarioConfig",
    positions: Mapping[str, Tuple[float, float]],
    *,
    excluded_subtask_ids: Optional[Iterable[str]] = None,
) -> List[str]:
    """Stable assignable subtask ids from scenario.subtasks order.

    ``excluded_subtask_ids`` are intentionally closed (for example yellow when
    no eligible robot remains). They are skipped without resolving coordinates.
    Every other subtask is resolved through ``resolve_subtask_target_xy``:
    targetless missions stay open with ``None`` targets; ordinary missions
    missing coordinates raise immediately instead of vanishing.
    """
    excluded = frozenset(excluded_subtask_ids or ())
    open_ids: List[str] = []
    for sid in scenario.subtasks:
        if sid in excluded:
            continue
        resolve_subtask_target_xy(scenario, sid, positions)
        open_ids.append(sid)
    return open_ids



@dataclass
class SubtaskConstraintSpec:
    """Extra constraint lines injected into the LLM scene description."""

    subtask_id: str
    title: str
    lines: List[str] = field(default_factory=list)


@dataclass
class EMOSScenarioConfig:
    """Declarative EMOS scenario: task background + subtasks + robots + rules."""

    scenario_id: str
    scene_title: str
    anchor_label: str
    task_description: str
    subtasks: Dict[str, SubtaskSpec]
    position_rules: Dict[str, PositionRule]
    robot_profiles: Dict[str, RobotProfileSpec]
    preferred_fallback: Dict[str, str]
    yellow_subtask_id: str = "yellow"
    yellow_robot_prefix: str = "m20"
    green_subtask_id: str = "green"
    green_robot_name: str = ""
    subtask_constraints: List[SubtaskConstraintSpec] = field(default_factory=list)
    parse_keyword_overrides: Optional[Dict[str, List[str]]] = None

    def subtask_tuple_map(self) -> Dict[str, Tuple[str, str]]:
        return {sid: (s.name, s.description) for sid, s in self.subtasks.items()}

    def keyword_map_for_parse(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for sid, spec in self.subtasks.items():
            keys = list(spec.match_keywords)
            if self.parse_keyword_overrides and sid in self.parse_keyword_overrides:
                keys = list(self.parse_keyword_overrides[sid])
            out[sid] = keys
        return out


@dataclass
class RobotTask:
    """One robot's assigned subtask from EMOS (algorithm layer).

    ``target_xy`` is ``None`` for targetless subtasks (no travel waypoint).
    """

    robot_name: str
    subtask_colour: str
    subtask_name: str
    subtask_desc: str
    target_xy: Optional[Tuple[float, float]]


@dataclass
class EMOSRobotAgentSpec:
    """Static resume for one robot: type, capabilities, optional preferred subtask.

    The caller (e.g. a simulation ``emos.py`` script) builds a dict of
    ``EMOSRobotAgentSpec`` and passes it to ``EMOSDiscussionManager.build_from_agent_specs``.
    It is not tied to Isaac controller tuples.

    Example::

        specs = {
            "m20_1": EMOSRobotAgentSpec(
                robot_type="四足机器人 M20",
                capabilities=["四足移动", "UR5 机械臂"],
                preferred_task="red",
            ),
        }

    The ``agent_name`` field is optional; ``build_from_agent_specs`` fills it from
    the dict key when left empty.
    """

    agent_name: str = ""  # optional; filled from dict key by build_from_agent_specs
    robot_type: str = ""
    capabilities: List[str] = field(default_factory=list)
    preferred_task: Optional[str] = None


def compute_subtask_positions(
    scenario: EMOSScenarioConfig,
    anchor_pos: Tuple[float, float, float],
    base_overrides: Optional[Dict[str, Tuple[float, float]]] = None,
) -> Dict[str, Tuple[float, float]]:
    """Resolve spatial subtask waypoints only.

    Targetless rules (``kind="none"`` / legacy ``fixed`` + ``xy=None``) are
    omitted from the result so callers cannot confuse them with ``(0, 0)``.
    """
    hx, hy = float(anchor_pos[0]), float(anchor_pos[1])
    out: Dict[str, Tuple[float, float]] = {}
    if base_overrides:
        out.update(base_overrides)
    for sid, rule in scenario.position_rules.items():
        validate_position_rule(rule, subtask_id=sid)
        if is_targetless_position_rule(rule):
            out.pop(sid, None)
            continue
        if rule.kind == "fixed" and rule.xy is not None:
            out[sid] = (float(rule.xy[0]), float(rule.xy[1]))
        elif rule.kind == "anchor_offset":
            out[sid] = (hx + rule.dx, hy + rule.dy)
        else:
            raise ValueError(f"unsupported position rule kind {rule.kind!r} for {sid!r}")
    return out


def scenario_from_dict(data: Dict[str, Any]) -> EMOSScenarioConfig:
    subtasks: Dict[str, SubtaskSpec] = {}
    for sid, raw in (data.get("subtasks") or {}).items():
        subtasks[sid] = SubtaskSpec(
            id=sid,
            name=str(raw.get("name", sid)),
            description=str(raw.get("description", "")),
            match_keywords=list(raw.get("match_keywords") or []),
        )

    pos_rules: Dict[str, PositionRule] = {}
    for sid, raw in (data.get("position_rules") or {}).items():
        if not isinstance(raw, dict):
            raise ValueError(f"position rule for {sid!r} must be an object")
        kind = str(raw.get("type", "fixed"))
        if kind in {"none", "targetless"}:
            pos_rules[sid] = PositionRule(kind="none", xy=None)
        elif kind == "fixed":
            if "xy" not in raw:
                raise ValueError(f"fixed position rule for {sid!r} requires xy")
            xy = raw.get("xy")
            if xy is None:
                # Explicit null xy remains the legacy targetless spelling.
                pos_rules[sid] = PositionRule(kind="none", xy=None)
            else:
                if not isinstance(xy, (list, tuple)) or len(xy) != 2:
                    raise ValueError(f"fixed position rule for {sid!r} needs a 2-number xy")
                pos_rules[sid] = PositionRule(
                    kind="fixed",
                    xy=(float(xy[0]), float(xy[1])),
                )
        elif kind == "anchor_offset":
            pos_rules[sid] = PositionRule(
                kind="anchor_offset",
                dx=float(raw.get("dx", 0.0)),
                dy=float(raw.get("dy", 0.0)),
            )
        else:
            raise ValueError(f"unknown position rule type {kind!r} for {sid!r}")

    profiles: Dict[str, RobotProfileSpec] = {}
    for rn, raw in (data.get("robot_profiles") or {}).items():
        profiles[rn] = RobotProfileSpec(
            robot_type=str(raw.get("type", rn)),
            capabilities=list(raw.get("capabilities") or []),
        )

    subtask_constraints: List[SubtaskConstraintSpec] = []
    for raw in data.get("subtask_constraints") or []:
        subtask_constraints.append(
            SubtaskConstraintSpec(
                subtask_id=str(raw.get("subtask_id", "")),
                title=str(raw.get("title", "")),
                lines=list(raw.get("lines") or []),
            )
        )

    return EMOSScenarioConfig(
        scenario_id=str(data.get("scenario_id", "default")),
        scene_title=str(data.get("scene_title", "Scene")),
        anchor_label=str(data.get("anchor_label", "event")),
        task_description=str(data.get("task_description", "")),
        subtasks=subtasks,
        position_rules=pos_rules,
        robot_profiles=profiles,
        preferred_fallback=dict(data.get("preferred_fallback") or {}),
        yellow_subtask_id=str(data.get("yellow_subtask_id", "yellow")),
        yellow_robot_prefix=str(data.get("yellow_robot_prefix", "m20")),
        green_subtask_id=str(data.get("green_subtask_id", "green")),
        green_robot_name=str(data.get("green_robot_name", "")),
        subtask_constraints=subtask_constraints,
        parse_keyword_overrides=data.get("parse_keyword_overrides"),
    )


def default_subtask_positions_pre_trigger(scenario: EMOSScenarioConfig) -> Dict[str, Tuple[float, float]]:
    out: Dict[str, Tuple[float, float]] = {}
    for sid, rule in scenario.position_rules.items():
        validate_position_rule(rule, subtask_id=sid)
        if is_targetless_position_rule(rule):
            continue
        if rule.kind == "fixed" and rule.xy is not None:
            out[sid] = (float(rule.xy[0]), float(rule.xy[1]))
        elif rule.kind == "anchor_offset":
            # Pre-trigger placeholder until the real anchor arrives.
            out[sid] = (0.0, 0.0)
        else:
            raise ValueError(f"unsupported position rule kind {rule.kind!r} for {sid!r}")
    return out
