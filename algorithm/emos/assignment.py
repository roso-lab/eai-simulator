"""Parse LLM outputs and heuristic fallback assignments."""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .types import EMOSScenarioConfig, RobotTask


def parse_discussion_results(
    scenario: EMOSScenarioConfig,
    results: Dict[str, Any],
    positions: Dict[str, Tuple[float, float]],
    agents: list[str],
    *,
    leader_log: Optional[Callable[..., None]] = None,
    push_chat: Callable[[str, str, str], None],
    generate_report: Callable[[Dict[str, RobotTask]], None],
    sanitize: Callable[[Dict[str, RobotTask], Dict[str, Tuple[float, float]]], Dict[str, RobotTask]],
    subtask_tuple_map: Dict[str, Tuple[str, str]],
    keyword_map: Dict[str, List[str]],
    agent_iteration_order: Optional[List[str]] = None,
    preserve_raw_assignments: bool = False,
) -> Dict[str, RobotTask]:
    available_ids = list(positions.keys())
    assignments: Dict[str, RobotTask] = {}

    for robot_name, agent_args in results.items():
        subtask = getattr(agent_args, "subtask_description", "").lower()
        matched: Optional[str] = None
        for sid, keywords in keyword_map.items():
            if sid not in available_ids:
                continue
            for kw in keywords:
                if kw.lower() in subtask:
                    matched = sid
                    break
            if matched:
                break
        if matched is None and available_ids and not preserve_raw_assignments:
            matched = available_ids[0]
        if matched:
            if matched in available_ids:
                available_ids.remove(matched)
            tn, td = subtask_tuple_map.get(matched, (matched, ""))
            assignments[robot_name] = RobotTask(
                robot_name=robot_name,
                subtask_colour=matched,
                subtask_name=tn,
                subtask_desc=getattr(agent_args, "subtask_description", td),
                target_xy=positions.get(matched, (0.0, 0.0)),
            )

    if not preserve_raw_assignments:
        fill_order = agent_iteration_order if agent_iteration_order is not None else agents
        for rname in fill_order:
            if rname not in assignments and available_ids:
                c = available_ids.pop(0)
                tn, td = subtask_tuple_map.get(c, (c, ""))
                assignments[rname] = RobotTask(
                    robot_name=rname,
                    subtask_colour=c,
                    subtask_name=tn,
                    subtask_desc=td,
                    target_xy=positions.get(c, (0.0, 0.0)),
                )

    final_assignments = assignments if preserve_raw_assignments else sanitize(assignments, positions)

    if leader_log:
        for rn, rt in final_assignments.items():
            leader_log(rn, rt.subtask_name, rt.subtask_colour, rt.target_xy)

    summary_lines = ["📋 任务分配结果："]
    for rn, rt in final_assignments.items():
        tpos = rt.target_xy
        summary_lines.append(
            f"  🤖 {rn} → {rt.subtask_name}（{rt.subtask_colour}）"
            f" 目标 ({tpos[0]:.1f}, {tpos[1]:.1f})"
        )
        summary_lines.append(f"     {rt.subtask_desc[:80]}")
    push_chat("emos", "EMOS 任务分配", "\n".join(summary_lines))

    generate_report(final_assignments)
    return final_assignments


def fallback_assignment(
    scenario: EMOSScenarioConfig,
    positions: Dict[str, Tuple[float, float]],
    agents: list[str],
    *,
    robots_completed_extinguisher: Set[str],
    robots_completed_rescue: Set[str],
    sys_log: Callable[[str], None],
    push_chat: Callable[[str, str, str], None],
    generate_report: Callable[[Dict[str, RobotTask]], None],
    color_for_agent: Optional[Callable[[str], str]] = None,
) -> Dict[str, RobotTask]:
    positions = filter_subtask_positions_for_completed(
        positions,
        scenario,
        agents,
        robots_completed_extinguisher,
        robots_completed_rescue,
    )
    colours = list(positions.keys())
    assignments: Dict[str, RobotTask] = {}
    blocked_y = robots_completed_extinguisher | robots_completed_rescue
    preferred = scenario.preferred_fallback
    used: set[str] = set()

    yid = scenario.yellow_subtask_id
    prefix = scenario.yellow_robot_prefix

    def _can_take_yellow(rn: str) -> bool:
        # 能力驱动的 yellow 任务分配：检查机器人能力而非前缀
        if rn in blocked_y:
            return False
        profile = scenario.robot_profiles.get(rn)
        if not profile:
            # 如果没有 profile 但名字匹配前缀，也允许（兼容旧逻辑）
            return rn.startswith(prefix)
        cap_text = " ".join(profile.capabilities).lower()
        # yellow 任务需要：四足移动/翻越 能力
        required_keywords = ["四足", "翻越", "quadruped"]
        return any(kw in cap_text for kw in required_keywords)

    gid = scenario.green_subtask_id
    green_target = (scenario.green_robot_name or "").strip()
    if (
        green_target
        and gid in colours
        and green_target in agents
        and green_target not in blocked_y
    ):
        st_map = scenario.subtask_tuple_map()
        gn, gd = st_map.get(gid, (gid, ""))
        assignments[green_target] = RobotTask(
            robot_name=green_target,
            subtask_colour=gid,
            subtask_name=gn,
            subtask_desc=gd,
            target_xy=positions.get(gid, (0.0, 0.0)),
        )
        used.add(gid)
        if color_for_agent:
            sys_log(f"  {color_for_agent(green_target)} → {gn}（{gid}）[预留 green]")
        else:
            sys_log(f"  {green_target} → {gn}（{gid}）[预留 green]")

    for rname in agents:
        if rname in assignments:
            continue
        c = preferred.get(rname)
        if c == yid and not _can_take_yellow(rname):
            c = None
        if c and c in colours and c not in used:
            pass
        else:
            c = None
            for cc in colours:
                if cc in used:
                    continue
                if cc == yid and not _can_take_yellow(rname):
                    continue
                c = cc
                break
        if c is None:
            continue
        used.add(c)
        st_map = scenario.subtask_tuple_map()
        tn, td = st_map.get(c, (c, ""))
        assignments[rname] = RobotTask(
            robot_name=rname,
            subtask_colour=c,
            subtask_name=tn,
            subtask_desc=td,
            target_xy=positions.get(c, (0.0, 0.0)),
        )
        if color_for_agent:
            sys_log(f"  {color_for_agent(rname)} → {tn}（{c}）")
        else:
            sys_log(f"  {rname} → {tn}（{c}）")

    fb_lines = ["📋 启发式任务分配结果："]
    for rn, rt in assignments.items():
        tpos = rt.target_xy
        fb_lines.append(
            f"  🤖 {rn} → {rt.subtask_name}（{rt.subtask_colour}）"
            f" 目标 ({tpos[0]:.1f}, {tpos[1]:.1f})"
        )
    push_chat("emos", "EMOS 任务分配", "\n".join(fb_lines))

    generate_report(assignments)
    return assignments


def filter_subtask_positions_for_completed(
    positions: Dict[str, Tuple[float, float]],
    scenario: EMOSScenarioConfig,
    agents: list[str],
    robots_completed_extinguisher: Set[str],
    robots_completed_rescue: Set[str],
) -> Dict[str, Tuple[float, float]]:
    """过滤已完成任务的子任务位置，能力驱动的 yellow 分配"""
    out = dict(positions)
    yid = scenario.yellow_subtask_id
    prefix = scenario.yellow_robot_prefix
    blocked = robots_completed_extinguisher | robots_completed_rescue

    # 能力驱动的 yellow 机器人筛选：检查能力而非前缀
    def _can_take_yellow(rn: str) -> bool:
        if rn in blocked:
            return False
        profile = scenario.robot_profiles.get(rn)
        if not profile:
            return rn.startswith(prefix)  # 兼容：没有 profile 时用前缀
        cap_text = " ".join(profile.capabilities).lower()
        required_keywords = ["四足", "翻越", "quadruped"]
        return any(kw in cap_text for kw in required_keywords)

    elig_yellow = [a for a in agents if _can_take_yellow(a)]
    if yid in out and not elig_yellow:
        out.pop(yid, None)
    return out


def sanitize_llm_assignments(
    scenario: EMOSScenarioConfig,
    assignments: Dict[str, RobotTask],
    positions: Dict[str, Tuple[float, float]],
    agents: list[str],
    robots_completed_extinguisher: Set[str],
    robots_completed_rescue: Set[str],
    fallback_fn: Callable[[Dict[str, Tuple[float, float]]], Dict[str, RobotTask]],
) -> Dict[str, RobotTask]:
    pos = filter_subtask_positions_for_completed(
        dict(positions), scenario, agents, robots_completed_extinguisher, robots_completed_rescue
    )
    blocked = robots_completed_extinguisher | robots_completed_rescue
    yid = scenario.yellow_subtask_id
    blue_id = "blue"
    red_id = "red"

    def _can_take_yellow(rn: str) -> bool:
        if rn in blocked:
            return False
        profile = scenario.robot_profiles.get(rn)
        if not profile:
            return rn.startswith(scenario.yellow_robot_prefix)
        cap_text = " ".join(profile.capabilities).lower()
        required_keywords = ["四足", "翻越", "quadruped"]
        return any(kw in cap_text for kw in required_keywords)

    def _profile_text(rn: str) -> str:
        profile = scenario.robot_profiles.get(rn)
        if not profile:
            return rn.lower()
        return f"{profile.robot_type} {' '.join(profile.capabilities)}".lower()

    def _can_take_blue(rn: str) -> bool:
        text = _profile_text(rn)
        return any(kw in text for kw in ("orsus", "传感", "数据采集", "雷达", "图像"))

    def _can_take_red(rn: str) -> bool:
        text = _profile_text(rn)
        return rn.startswith(scenario.yellow_robot_prefix) or any(
            kw in text for kw in ("m20", "四足", "翻越", "quadruped")
        )

    def _swap_tasks(a: str, b: str) -> None:
        ta = assignments[a]
        tb = assignments[b]
        assignments[a] = RobotTask(
            robot_name=a,
            subtask_colour=tb.subtask_colour,
            subtask_name=tb.subtask_name,
            subtask_desc=tb.subtask_desc,
            target_xy=tb.target_xy,
        )
        assignments[b] = RobotTask(
            robot_name=b,
            subtask_colour=ta.subtask_colour,
            subtask_name=ta.subtask_name,
            subtask_desc=ta.subtask_desc,
            target_xy=ta.target_xy,
        )

    for rn, rt in assignments.items():
        if rt.subtask_colour == yid and not _can_take_yellow(rn):
            return fallback_fn(pos)
    blue_robot = next((rn for rn, rt in assignments.items() if rt.subtask_colour == blue_id), None)
    if blue_robot and not _can_take_blue(blue_robot):
        candidate = next(
            (
                rn
                for rn, rt in assignments.items()
                if rn not in blocked and _can_take_blue(rn) and rt.subtask_colour != yid
            ),
            None,
        )
        if candidate:
            _swap_tasks(blue_robot, candidate)
    red_robot = next((rn for rn, rt in assignments.items() if rt.subtask_colour == red_id), None)
    if red_robot and not _can_take_red(red_robot):
        candidate = next(
            (
                rn
                for rn, rt in assignments.items()
                if rn not in blocked and _can_take_red(rn) and rt.subtask_colour not in {yid, scenario.green_subtask_id}
            ),
            None,
        )
        if candidate:
            _swap_tasks(red_robot, candidate)
    return assignments


def validate_and_fix_yellow_assignment(
    scenario: EMOSScenarioConfig,
    assignments: Dict[str, RobotTask],
    positions: Dict[str, Tuple[float, float]],
    agents: list[str],
    get_robot_pos: Callable[[str], Tuple[float, float, float]],
    robots_completed_extinguisher: Set[str],
    robots_completed_rescue: Set[str],
    push_chat: Callable[[str, str, str], None],
    sanitize: Callable[[Dict[str, RobotTask], Dict[str, Tuple[float, float]]], Dict[str, RobotTask]],
) -> Tuple[Dict[str, RobotTask], Optional[str]]:
    yid = scenario.yellow_subtask_id
    prefix = scenario.yellow_robot_prefix

    yellow_robot = None
    for rname, task in assignments.items():
        if task.subtask_colour == yid:
            yellow_robot = rname
            break

    if yellow_robot and not yellow_robot.startswith(prefix):
        push_chat(
            "emos",
            "EMOS 系统",
            f"⚠️ LLM 将 {yid} 子任务分配给了 {yellow_robot}（非 {prefix}），"
            f"已自动修正为最近的可用机器人。",
        )
        ext_pos = positions.get(yid, (1.77, -9.38))
        candidates = [a for a in agents if a.startswith(prefix)]
        best = None
        best_dist = float("inf")
        blocked_y = robots_completed_extinguisher | robots_completed_rescue
        for r in candidates:
            if r in blocked_y:
                continue
            try:
                p = get_robot_pos(r)
                d = math.hypot(p[0] - ext_pos[0], p[1] - ext_pos[1])
                if d < best_dist:
                    best_dist = d
                    best = r
            except Exception:
                pass

        if best:
            old_yellow_task = assignments[yellow_robot]
            new_yellow_task = RobotTask(
                robot_name=best,
                subtask_colour=yid,
                subtask_name=old_yellow_task.subtask_name,
                subtask_desc=old_yellow_task.subtask_desc,
                target_xy=positions.get(yid, ext_pos),
            )
            if best in assignments:
                old_other = assignments[best]
                assignments[yellow_robot] = RobotTask(
                    robot_name=yellow_robot,
                    subtask_colour=old_other.subtask_colour,
                    subtask_name=old_other.subtask_name,
                    subtask_desc=old_other.subtask_desc,
                    target_xy=old_other.target_xy,
                )
            assignments[best] = new_yellow_task

    assignments = sanitize(assignments, positions)
    ext_robot = next((rn for rn, t in assignments.items() if t.subtask_colour == yid), None)
    if ext_robot:
        push_chat(
            "emos",
            "EMOS 系统",
            f"🔥 灭火器运送任务已分配给 {ext_robot}。\n理由：具备任务所需机动与操作能力。",
        )
    return assignments, ext_robot


def validate_and_fix_green_assignment(
    scenario: EMOSScenarioConfig,
    assignments: Dict[str, RobotTask],
    positions: Dict[str, Tuple[float, float]],
    agents: list[str],
    robots_completed_extinguisher: Set[str],
    robots_completed_rescue: Set[str],
    push_chat: Callable[[str, str, str], None],
    sanitize: Callable[[Dict[str, RobotTask], Dict[str, Tuple[float, float]]], Dict[str, RobotTask]],
) -> Dict[str, RobotTask]:
    """If scenario.green_robot_name is set, ensure the green subtask is assigned to that robot (swap if needed).

    Skips forcing the swap when the target robot is blocked (extinguisher/rescue completed sets)
    or not in ``agents``. Mirrors :func:`validate_and_fix_yellow_assignment` swap logic.
    """
    gid = scenario.green_subtask_id
    target = (scenario.green_robot_name or "").strip()
    if not target:
        return sanitize(assignments, positions)

    green_robot = None
    for rname, task in assignments.items():
        if task.subtask_colour == gid:
            green_robot = rname
            break

    if not green_robot or green_robot == target:
        return sanitize(assignments, positions)

    blocked = robots_completed_extinguisher | robots_completed_rescue
    if target not in agents or target in blocked:
        push_chat(
            "emos",
            "EMOS 系统",
            f"⚠️ {gid} 子任务当前在 {green_robot}，但目标机体 {target} 不可用，未强制改派。",
        )
        return sanitize(assignments, positions)

    push_chat(
        "emos",
        "EMOS 系统",
        f"⚠️ LLM 将 {gid} 子任务分配给了 {green_robot}（应为 {target}），已自动交换任务。",
    )
    green_pos = positions.get(gid, (10.58, 1.0))
    old_green_task = assignments[green_robot]
    new_green_task = RobotTask(
        robot_name=target,
        subtask_colour=gid,
        subtask_name=old_green_task.subtask_name,
        subtask_desc=old_green_task.subtask_desc,
        target_xy=positions.get(gid, green_pos),
    )
    if target in assignments:
        old_other = assignments[target]
        assignments[green_robot] = RobotTask(
            robot_name=green_robot,
            subtask_colour=old_other.subtask_colour,
            subtask_name=old_other.subtask_name,
            subtask_desc=old_other.subtask_desc,
            target_xy=old_other.target_xy,
        )
    else:
        yid = scenario.yellow_subtask_id
        used_by_others = {
            assignments[r].subtask_colour
            for r in assignments
            if r != green_robot
        }

        def _can_take_yellow_fix(rn: str) -> bool:
            if rn in blocked:
                return False
            profile = scenario.robot_profiles.get(rn)
            if not profile:
                return rn.startswith(scenario.yellow_robot_prefix)
            cap_text = " ".join(profile.capabilities).lower()
            return any(kw in cap_text for kw in ("四足", "翻越", "quadruped"))

        st_map = scenario.subtask_tuple_map()
        replacement: Optional[str] = None
        for cid in positions.keys():
            if cid == gid:
                continue
            if cid in used_by_others:
                continue
            if cid == yid and not _can_take_yellow_fix(green_robot):
                continue
            replacement = cid
            break
        if replacement is not None:
            tn, td = st_map.get(replacement, (replacement, ""))
            assignments[green_robot] = RobotTask(
                robot_name=green_robot,
                subtask_colour=replacement,
                subtask_name=tn,
                subtask_desc=td,
                target_xy=positions.get(replacement, (0.0, 0.0)),
            )
        else:
            # 无空闲颜色时：将 yellow 改派到可领 yellow 的机体，原机体颜色让给 green 持有者
            donor: Optional[str] = None
            if yid in positions:
                for rn in agents:
                    if rn in (green_robot, target) or rn not in assignments:
                        continue
                    if assignments[rn].subtask_colour == yid:
                        continue
                    if not _can_take_yellow_fix(rn):
                        continue
                    donor = rn
                    break
            if donor is not None:
                old_task = assignments[donor]
                old_c = old_task.subtask_colour
                tn_y, td_y = st_map.get(yid, (yid, ""))
                assignments[donor] = RobotTask(
                    robot_name=donor,
                    subtask_colour=yid,
                    subtask_name=tn_y,
                    subtask_desc=td_y,
                    target_xy=positions.get(yid, (0.0, 0.0)),
                )
                tn_o, td_o = st_map.get(old_c, (old_c, ""))
                assignments[green_robot] = RobotTask(
                    robot_name=green_robot,
                    subtask_colour=old_c,
                    subtask_name=tn_o,
                    subtask_desc=td_o,
                    target_xy=positions.get(old_c, (0.0, 0.0)),
                )
            else:
                del assignments[green_robot]
                push_chat(
                    "emos",
                    "EMOS 系统",
                    f"⚠️ 无法为 {green_robot} 分配非 green 子任务，已仅将 green 分配给 {target}。",
                )
    assignments[target] = new_green_task

    assignments = sanitize(assignments, positions)
    rc = next((rn for rn, t in assignments.items() if t.subtask_colour == gid), None)
    if rc:
        push_chat(
            "emos",
            "EMOS 系统",
            f"🟢 打开救援通道任务已分配给 {rc}。\n理由：仿真中救援通道按钮与该机 UR5 绑定。",
        )
    return assignments
