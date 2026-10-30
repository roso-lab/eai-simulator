"""EMOS scenario configuration types (task background, subtasks, robots, constraints)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Literal, Optional, Tuple


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


@dataclass
class PositionRule:
    """How to compute (x, y) for a subtask when an anchor event fires."""

    kind: Literal["fixed", "anchor_offset"]
    xy: Optional[Tuple[float, float]] = None
    dx: float = 0.0
    dy: float = 0.0


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
    """One robot's assigned subtask from EMOS (algorithm layer)."""

    robot_name: str
    subtask_colour: str
    subtask_name: str
    subtask_desc: str
    target_xy: Tuple[float, float]


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
    hx, hy = float(anchor_pos[0]), float(anchor_pos[1])
    out: Dict[str, Tuple[float, float]] = {}
    if base_overrides:
        out.update(base_overrides)
    for sid, rule in scenario.position_rules.items():
        if rule.kind == "fixed" and rule.xy is not None:
            out[sid] = (float(rule.xy[0]), float(rule.xy[1]))
        elif rule.kind == "anchor_offset":
            out[sid] = (hx + rule.dx, hy + rule.dy)
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
        kind = str(raw.get("type", "fixed"))
        if kind == "fixed":
            xy = raw.get("xy") or [0.0, 0.0]
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
            pos_rules[sid] = PositionRule(kind="fixed", xy=(0.0, 0.0))

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
        if rule.kind == "fixed" and rule.xy is not None:
            out[sid] = (float(rule.xy[0]), float(rule.xy[1]))
        elif rule.kind == "anchor_offset":
            # 不再硬编码 blue 位置，返回 (0, 0) 作为占位
            out[sid] = (0.0, 0.0)
        else:
            out[sid] = (0.0, 0.0)
    return out
