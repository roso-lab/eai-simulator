from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from .algorithm_paths import ensure_fire_rescue_algorithm_paths
from .config import EXTINGUISHER_PICKUP_XY, RESCUE_CHANNEL_BUTTON_XY

ensure_fire_rescue_algorithm_paths()

FACTORY_EMOS_SCENARIO_DICT: Dict[str, Any] = {
    "scenario_id": "emos_factory",
    "scene_title": "工厂环境（Factory）",
    "anchor_label": "火源",
    "task_description": (
        "工厂内出现火源（危险柱），需要多机器人协作完成救援任务。\n"
        "任务包括：\n"
        "1) 前往火源位置附近进行侦查（四足机器人可翻越障碍更灵活）；\n"
        "2) 打开救援通道（必须由 Scout 差速移动底盘 scout_1 使用背部 UR5 敲击救援通道按钮）；\n"
        "3) 使用搭载传感器的无人车传回现场图像与数据；\n"
        "4) 携带灭火器前往火源附近（需要四足机器人用机械臂夹取灭火器）。\n"
        "请根据各机器人的能力和当前位置，合理分配上述子任务。"
    ),
    "subtasks": {
        "red": {
            "name": "火源侦查",
            "description": "前往火源位置附近进行侦查，需可翻越障碍。适合四足机器人。",
            "match_keywords": ["red", "红", "火源", "侦查", "翻越", "台阶"],
        },
        "green": {
            "name": "打开救援通道",
            "description": (
                "前往救援通道按钮处，用背部 UR5 机械臂敲击按钮打开通道。"
                "需 Scout 差速移动底盘；本场景中 green 子任务分配给 scout_1（Scout + UR5），"
                "不得分配给 carter_1。"
            ),
            "match_keywords": ["green", "绿", "救援通道", "按钮", "通道", "敲击"],
        },
        "blue": {
            "name": "数据采集",
            "description": "前往现场采集图像与雷达数据并回传，需传感器套件。",
            "match_keywords": ["blue", "蓝", "数据", "采集", "图像", "雷达", "传感", "gs-hub", "gshub"],
        },
        "yellow": {
            "name": "灭火器运送",
            "description": "夹取灭火器并运送到火源附近，需机械臂+移动能力。适合四足机器人。",
            "match_keywords": ["yellow", "黄", "灭火", "夹取", "运送", "extinguish"],
        },
    },
    "position_rules": {
        "red": {"type": "anchor_offset", "dx": 1.5, "dy": 0.0},
        "blue": {"type": "anchor_offset", "dx": -1.5, "dy": 0.0},
        "green": {"type": "fixed", "xy": list(RESCUE_CHANNEL_BUTTON_XY)},
        "yellow": {"type": "fixed", "xy": list(EXTINGUISHER_PICKUP_XY)},
    },
    "robot_profiles": {},
    "preferred_fallback": {},
    "yellow_subtask_id": "yellow",
    "yellow_robot_prefix": "m20",
    "green_subtask_id": "green",
    "green_robot_name": "scout_1",
    "subtask_constraints": [
        {
            "subtask_id": "green",
            "title": "打开救援通道约束：",
            "lines": [
                "救援通道按钮仅由 scout_1（Scout + 背部 UR5）执行敲击",
                "carter_1 搭载 GS-Hub 传感器，无用于敲击的 UR5 臂，禁止分配 green 子任务",
                "仿真中绿色任务与 scout_1 的机械臂控制器绑定，分配错误时系统会自动改派",
            ],
        },
        {
            "subtask_id": "yellow",
            "title": "灭火器运送约束：",
            "lines": [
                "要求机器人具备「可翻越台阶」能力",
                "只有 M20 系列机器人（m20_1, m20_2）满足此条件",
                "Carter 与 Scout（carter_1, scout_1）不具备此能力，禁止分配 yellow 子任务",
            ],
        },
    ],
}


FACTORY_EMOS_AGENT_RESUMES: Dict[str, Dict[str, List[str] | str]] = {
    "m20_1": {
        "robot_type": "四足机器人 M20",
        "capabilities": [
            "四足移动，可翻越台阶与复杂地形",
            "搭载 UR5 机械臂，可执行抓取与按压操作",
            "搭载深度摄像头",
        ],
    },
    "m20_2": {
        "robot_type": "四足机器人 M20",
        "capabilities": [
            "四足移动，可翻越台阶与复杂地形",
            "搭载 UR5 机械臂，可执行抓取与按压操作",
            "搭载深度摄像头",
        ],
    },
    "carter_1": {
        "robot_type": "Carter 差速无人车",
        "capabilities": [
            "轮式移动，体型较小，速度快但仅限平坦地面",
            "搭载 GS-Hub 传感器套件（摄像头+雷达点云）",
            "适合数据采集与传输任务",
        ],
    },
    "scout_1": {
        "robot_type": "Scout 平台 + UR5 机械臂",
        "capabilities": [
            "轮式移动，体型较大，只可在平坦地面运动",
            "搭载 UR5 机械臂，可抓取灭火器等物体",
            "搭载摄像头",
        ],
    },
}


FACTORY_EMOS_PREFERRED_FALLBACK: Dict[str, str] = {
    "m20_1": "red",
    "m20_2": "yellow",
    "carter_1": "blue",
    "scout_1": "green",
}


class FireRescueEventKind(str, Enum):
    HAZARD_SCOUT = "hazard_scout"
    OPEN_RESCUE_CHANNEL = "open_rescue_channel"
    DATA_RELAY = "data_relay"
    EXTINGUISHER_RUN = "extinguisher_run"
    UNKNOWN = "unknown"


COLOUR_TO_EVENT = {
    "red": FireRescueEventKind.HAZARD_SCOUT,
    "green": FireRescueEventKind.OPEN_RESCUE_CHANNEL,
    "blue": FireRescueEventKind.DATA_RELAY,
    "yellow": FireRescueEventKind.EXTINGUISHER_RUN,
}


@dataclass
class FireRescueInterpretedTask:
    robot_name: str
    robot_type_label: str
    subtask_colour: str
    subtask_name: str
    target_xy: Tuple[float, float]
    event_kind: FireRescueEventKind


def build_factory_emos_scenario():
    from algorithm.emos.types import scenario_from_dict

    data = dict(FACTORY_EMOS_SCENARIO_DICT)
    data["preferred_fallback"] = dict(FACTORY_EMOS_PREFERRED_FALLBACK)
    return scenario_from_dict(data)


def build_factory_emos_agent_specs():
    from algorithm.emos.types import EMOSRobotAgentSpec

    specs = {}
    for name, resume in FACTORY_EMOS_AGENT_RESUMES.items():
        capabilities = resume.get("capabilities")
        if not isinstance(capabilities, list):
            capabilities = []
        specs[name] = EMOSRobotAgentSpec(
            agent_name=name,
            robot_type=str(resume.get("robot_type", "")),
            capabilities=[str(item) for item in capabilities],
            preferred_task=FACTORY_EMOS_PREFERRED_FALLBACK.get(name),
        )
    return specs


def build_factory_emos_manager(
    base_env: Any,
    *,
    push_chat_fn: Optional[Callable[[str, str, str], None]] = None,
    log_dir: str = "logs/emos_reports",
    discussion_log_dir: str = "logs/multi_llm_demo",
    use_env_resume: bool = False,
    write_html_report: bool = False,
    get_group_discussion_llm_kwargs: Optional[Callable[[], Dict[str, Any]]] = None,
):
    from algorithm.emos.engine import EMOSDiscussionManager

    return EMOSDiscussionManager.build_from_agent_specs(
        base_env=base_env,
        agent_specs=build_factory_emos_agent_specs(),
        scenario=build_factory_emos_scenario(),
        push_chat_fn=push_chat_fn,
        log_dir=log_dir,
        discussion_log_dir=discussion_log_dir,
        use_env_resume=use_env_resume,
        write_html_report=write_html_report,
        get_group_discussion_llm_kwargs=get_group_discussion_llm_kwargs,
    )


def interpret_factory_tasks(result: Dict[str, Any]) -> List[FireRescueInterpretedTask]:
    interpreted: List[FireRescueInterpretedTask] = []
    for robot_name, task in result.items():
        raw = FACTORY_EMOS_AGENT_RESUMES.get(robot_name, {})
        colour = str(getattr(task, "subtask_colour", "") or "").lower().strip()
        interpreted.append(
            FireRescueInterpretedTask(
                robot_name=robot_name,
                robot_type_label=str(raw.get("robot_type", robot_name)),
                subtask_colour=str(getattr(task, "subtask_colour", "")),
                subtask_name=str(getattr(task, "subtask_name", "")),
                target_xy=tuple(getattr(task, "target_xy", (0.0, 0.0))),
                event_kind=COLOUR_TO_EVENT.get(colour, FireRescueEventKind.UNKNOWN),
            )
        )
    return interpreted
