"""Build LLM scene_description from EMOSScenarioConfig and runtime state."""

from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from .types import EMOSScenarioConfig


def build_scene_description(
    scenario: EMOSScenarioConfig,
    *,
    hazard_id: int,
    anchor_pos: Tuple[float, float, float],
    agents: list[str],
    robot_positions: Dict[str, Tuple[float, float]],
    subtask_positions: Dict[str, Tuple[float, float]],
    active_rescue_extra: Optional[str] = None,
) -> str:
    lines = [
        f"场景：{scenario.scene_title}，发现{scenario.anchor_label}，需要多机器人协作完成任务。\n",
        f"{scenario.anchor_label}位置（标记 #{hazard_id}）: "
        f"({anchor_pos[0]:.2f}, {anchor_pos[1]:.2f})\n",
        "\n子任务与对应目标位置：",
    ]
    st_map = scenario.subtask_tuple_map()
    for sid in sorted(scenario.subtasks.keys()):
        name, desc = st_map.get(sid, (sid, ""))
        pos = subtask_positions.get(sid, (0.0, 0.0))
        lines.append(
            f"  - {name}（{sid} 标记）: 位置 ({pos[0]:.2f}, {pos[1]:.2f})"
        )
        lines.append(f"    要求: {desc}")

    lines.append("\n机器人当前位置与能力：")
    for rname in agents:
        rpos = robot_positions.get(rname, (0.0, 0.0))
        profile = scenario.robot_profiles.get(rname)
        rtype = profile.robot_type if profile else rname
        caps = "、".join(profile.capabilities) if profile else ""
        lines.append(f"  - {rname} ({rtype}): 位置 ({rpos[0]:.2f}, {rpos[1]:.2f})")
        lines.append(f"    能力: {caps}")

    if active_rescue_extra:
        lines.append(active_rescue_extra)

    for block in scenario.subtask_constraints:
        lines.append(f"\n{block.title}")
        for ln in block.lines:
            lines.append(f"  {ln}")

    return "\n".join(lines)


def rescue_manager_constraint_text(rescue_mgr) -> Optional[str]:
    if rescue_mgr is None or not getattr(rescue_mgr, "is_active", False):
        return None
    rescue_robot = rescue_mgr.state.rescue_robot
    if not rescue_robot:
        # MONITORING 等阶段尚无救援机，勿向 LLM 注入「None 正在救援」的伪约束
        return None
    rescue_phase = rescue_mgr.state.phase.name
    lines = [
        "\n⚠️ 当前活跃救援任务约束（最高优先级）：",
        f"  - {rescue_robot} 正在执行障碍物救援任务（阶段: {rescue_phase}）",
        f"  - 禁止将任何新任务分配给 {rescue_robot}，直到救援完成",
        f"  - 如有子任务原本分配给 {rescue_robot}，必须重新选择其他可用机器人",
    ]
    return "\n".join(lines)


def make_rescue_constraint_getter(rescue_mgr) -> Callable[[], Optional[str]]:
    return lambda: rescue_manager_constraint_text(rescue_mgr)
