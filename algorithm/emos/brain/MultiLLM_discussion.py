import os
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# 支持作为包导入或直接运行
try:
    from .API.Model_API import DeepSeekModel as OpenAIModel
except ImportError:
    from API.Model_API import DeepSeekModel as OpenAIModel

ROBOT_RESUME_TEMPLATE = (
    '"{robot_key}" 是一个 "{robot_type}" 类型的智能体。'
    "它具有以下能力：\n\n"
    '"""\n{capabilities}\n"""\n\n'
)


class LeaderResponseFormatError(ValueError):
    """Raised when the leader reply cannot be converted into robot tasks."""


def _extract_radxa_scene_hints(scene_description: str) -> tuple[List[str], List[str]]:
    scene = scene_description or ""
    target_names: List[str] = []
    in_target_section = False
    for line in scene.splitlines():
        stripped = line.strip()
        if "子任务与对应目标位置" in stripped or stripped.startswith("目标条件"):
            in_target_section = True
            continue
        if in_target_section and stripped and not stripped.startswith(("-", "*")) and stripped.endswith(("：", ":")):
            in_target_section = False
        if not in_target_section:
            continue
        match = re.match(r"\s*[-*]\s*([^（(:：\n]+?)\s*(?:[（(:：]|$)", line)
        if not match:
            continue
        name = match.group(1).strip(" -\t")
        if (
            name
            and len(name) <= 20
            and name not in target_names
            and not re.fullmatch(r"[A-Za-z0-9_-]+", name)
        ):
            target_names.append(name)

    # Fallback for compact scene descriptions that list targets inline.
    for name in ("火源侦查", "灭火器运送", "数据采集", "打开救援通道"):
        if name in scene and name not in target_names:
            target_names.append(name)

    unavailable: List[str] = []
    unavailable_markers = ("禁止将任何新任务分配给", "禁止分配新任务", "当前不可用", "正在执行")
    for line in scene.splitlines():
        if not any(marker in line for marker in unavailable_markers):
            continue
        for robot_id in re.findall(r"\b[A-Za-z][A-Za-z0-9_-]*_\d+\b", line):
            if robot_id not in unavailable:
                unavailable.append(robot_id)
    return target_names, unavailable


def _extract_radxa_target_requirements(scene_description: str) -> List[tuple[str, str]]:
    scene = scene_description or ""
    targets: List[tuple[str, str]] = []
    current_name: Optional[str] = None
    current_requirement = ""
    in_target_section = False

    def _append_current() -> None:
        nonlocal current_name, current_requirement
        if current_name and not any(name == current_name for name, _ in targets):
            targets.append((current_name, current_requirement.strip()))
        current_name = None
        current_requirement = ""

    for line in scene.splitlines():
        stripped = line.strip()
        if "子任务与对应目标位置" in stripped or stripped.startswith("目标条件"):
            in_target_section = True
            continue
        if in_target_section and stripped and not stripped.startswith(("-", "*")) and stripped.endswith(("：", ":")):
            _append_current()
            in_target_section = False
        if not in_target_section:
            continue

        match = re.match(r"\s*[-*]\s*([^（(:：\n]+?)\s*(?:[（(:：]|$)", line)
        if match:
            _append_current()
            current_name = match.group(1).strip(" -\t")
            continue

        req_match = re.match(r"\s*要求\s*[:：]\s*(.+?)\s*$", line)
        if req_match and current_name:
            current_requirement = req_match.group(1).strip()

    _append_current()

    if targets:
        return targets

    target_names, _ = _extract_radxa_scene_hints(scene_description)
    return [(name, "") for name in target_names]


def _extract_radxa_hard_constraints(scene_description: str) -> List[str]:
    constraints: List[str] = []
    markers = (
        "最高优先级",
        "禁止",
        "必须",
        "仅由",
        "不具备",
        "要求机器人具备",
        "不可用",
        "正在执行",
    )
    for line in (scene_description or "").splitlines():
        stripped = line.strip(" -\t")
        if not stripped:
            continue
        if any(marker in stripped for marker in markers) and stripped not in constraints:
            constraints.append(stripped)
    return constraints


def _radxa_structured_scene_summary(scene_description: str) -> str:
    targets = _extract_radxa_target_requirements(scene_description)
    constraints = _extract_radxa_hard_constraints(scene_description)
    if not targets and not constraints:
        return ""

    lines = ["Radxa 结构化读题摘要："]
    if targets:
        lines.append("目标名称只能逐字复制以下清单，不要把目标名称改成近义词、同音词或错别字：")
        for name, requirement in targets:
            if requirement:
                lines.append(f"- {name}：{requirement}")
            else:
                lines.append(f"- {name}")
    if constraints:
        lines.append("硬约束原文摘录：")
        for item in constraints:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _radxa_scene_hint_text(scene_description: str) -> str:
    target_names, unavailable = _extract_radxa_scene_hints(scene_description)
    lines = []
    if target_names:
        lines.append("已提取的短目标名称清单：" + "、".join(target_names))
    if unavailable:
        lines.append("已提取的不可用机器人清单：" + "、".join(unavailable))
    return "\n".join(lines)


def _radxa_leader_output_constraints(robot_ids: Optional[List[str]] = None) -> str:
    allowed = "、".join(robot_ids or [])
    allowed_line = f"只能使用这些 robot_id：{allowed}。\n" if allowed else ""
    return (
        "\n本地 Radxa LLM 输出约束（必须遵守）：\n"
        "分配前请先确认所有目标条件，再为机器人分配；最终只输出分配行，不要写出分析过程。\n"
        "内部检查步骤：先标记不可用机器人；再提取场景中的原始目标条件名称；再逐项匹配目标硬约束和机器人能力；"
        "再根据你的判断决定是否分配目标或输出「无需执行」；最后检查输出格式必须稳定。\n"
        "在心中逐项检查原始目标条件，像清单一样确认你的分配是否符合自己的判断。\n"
        "尽可能让每个可用机器人都有明确任务；如果可用机器人少于目标条件，或可用机器人数量不足以一人一目标，可以把多个目标条件合并给同一个能力匹配的机器人。\n"
        "如果同一个机器人需要承担多个目标，请尽量把这些原始目标条件名称合并在同一条子任务描述中；也允许输出「无需执行」。\n"
        "可以输出模型认为合理的分配，本地流程不会用本地规则替你修正语义错误。\n"
        "只输出任务分配本身，不要输出 JSON、Markdown、编号、解释、思考过程、引号或逗号。\n"
        "每一行只能是一条任务分配，格式必须是 {robot_id||子任务描述}。\n"
        "每个参与讨论的机器人必须且只能出现一次，禁止编造新的 robot_id。\n"
        f"{allowed_line}"
        "禁止输出字面量 robot_id，必须使用上面列出的真实 robot_id。\n"
        "目标名称建议从原始目标条件中逐字复制，避免创造新的目标或任务类型。\n"
        "如果目标条件是火源侦查、灭火器运送、数据采集、打开救援通道，建议逐字保留这些任务名称。\n"
    )


def create_leader_prompt(robot_resume, robot_ids: Optional[List[str]] = None, llm_output_profile: Optional[str] = None):

    LEADER_SYSTEM_PROMPT_TEMPLATE = (
        "你是一个多机器人协同讨论的组长（Leader）。"
        "你需要与真实的机器人（智能体）讨论，将一个总体任务拆分成若干子任务，并为每个智能体分配一个子任务。"
        "参与讨论的智能体描述如下：\n\n"
        '"""\n{robot_resume}\n"""\n\n'
    )
    FORMAT_INSTRUCTION = (
        "请根据每个智能体的能力，为其分配子任务，严格按照以下格式输出：\n\n"
        r"{robot_id||子任务描述}\n\n"
        "注意：你必须使用大括号，并且必须在回复中包含所有机器人和所有目标条件。\n"
        "即使你认为某个机器人不需要执行任务，也请分配 {robot_id||无需执行}。\n"
        "即使你认为某个机器人需要执行多个子任务，也请将其合并为一条 {robot_id||子任务描述} 格式。\n"
        "子任务描述中应始终包含具体的「物体」或「目标」，不要只写「区域」。\n"
        "重要：所有子任务描述必须使用简体中文。\n"
    )
    RADXA_FORMAT_INSTRUCTION = ""
    if llm_output_profile == "radxa-local":
        RADXA_FORMAT_INSTRUCTION = _radxa_leader_output_constraints(robot_ids)

    return LEADER_SYSTEM_PROMPT_TEMPLATE.format(
        robot_resume=robot_resume) + FORMAT_INSTRUCTION + RADXA_FORMAT_INSTRUCTION

def create_leader_start_message(
    task_description,
    scene_description,
    llm_output_profile: Optional[str] = None,
):

    LEADER_MESSAGE_TEMPLATE = (
        "需要完成的任务描述如下：\n\n"
        '"""\n{task_description}\n"""\n\n'
        "所有智能体所在的场景描述如下：\n\n"
        '"""\n{scene_description}\n"""\n\n'
        "现在请根据每个智能体的能力，按照系统提示中的格式为其分配子任务。请用中文回复。"
    )

    message = LEADER_MESSAGE_TEMPLATE.format(
        task_description=task_description,
        scene_description=scene_description
    )
    if llm_output_profile == "radxa-local":
        hints = _radxa_scene_hint_text(scene_description)
        structured_summary = _radxa_structured_scene_summary(scene_description)
        message += (
            "\n\n本地 Radxa LLM 读题顺序（必须遵守）："
            "优先读取场景描述中的「子任务与对应目标位置」或「目标条件」，从每一项中提取短目标名称；"
            "先处理当前活跃救援任务约束和特殊约束，再按能力分配目标；"
            "子任务描述建议优先写短目标名称或短目标名称组合；"
            "最后确认每个 robot_id 都有一行，若你判断某个机器人不执行任务，可以输出「无需执行」。"
        )
        if hints:
            message += "\n" + hints
        if structured_summary:
            message += "\n\n" + structured_summary
    return message


def create_leader_self_reflection_message(
    initial_response: str,
    task_description: str,
    scene_description: str,
    robot_ids: List[str],
) -> str:
    hints = _radxa_scene_hint_text(scene_description)
    structured_summary = _radxa_structured_scene_summary(scene_description)
    allowed = "、".join(robot_ids)
    return (
        "请对你刚才的任务分配进行一次内部自检反思，然后重新输出最终任务分配。\n"
        "不要复述初稿，不要输出反思过程，只输出最终分配行。\n"
        "自检重点：目标清单是否全部覆盖；不可用机器人是否适合继续执行；"
        "特殊约束是否满足；子任务描述是否只使用原始短目标名称；格式是否每个机器人一行。\n"
        "发现缺失目标时请判断是否需要调整；"
        "可以把多个缺失目标合并给同一个能力匹配的可用机器人；"
        "也可以让任意机器人输出「无需执行」。\n"
        f"只能使用这些 robot_id：{allowed}。\n"
        f"{hints}\n"
        f"{structured_summary}\n"
        "原始任务描述：\n"
        f'"""\n{task_description}\n"""\n'
        "原始场景描述：\n"
        f'"""\n{scene_description}\n"""\n'
        "你的上一版分配：\n"
        f'"""\n{initial_response}\n"""\n'
        "现在请重新输出最终任务分配。"
        + _radxa_leader_output_constraints(robot_ids)
    )


def create_leader_assignment_review_message(
    initial_response: str,
    task_description: str,
    scene_description: str,
    robot_ids: List[str],
) -> str:
    hints = _radxa_scene_hint_text(scene_description)
    structured_summary = _radxa_structured_scene_summary(scene_description)
    allowed = "、".join(robot_ids)
    return (
        "请对上一版任务分配进行一次检查和反思。本轮不是最终答案，"
        "可以输出检查和反思过程，用于下一轮生成最终分配。\n"
        "重要：本轮不要使用 {robot_id||子任务描述} 格式，禁止使用大括号，"
        "不要输出任何可被当作最终任务分配的行。\n"
        "请按普通中文短句检查：\n"
        "1. 逐项检查每个目标条件是否已经覆盖。\n"
        "2. 写出每个目标的能力要求和候选机器人，注意不可用机器人不能接新任务。\n"
        "3. 写出上一版分配缺失或冲突的地方。\n"
        "4. 给出调整原则：尽可能让每个可用机器人都有任务；可用机器人少于目标时，"
        "可把多个目标合并给同一个能力匹配机器人。\n"
        f"只能考虑这些 robot_id：{allowed}。\n"
        f"{hints}\n"
        f"{structured_summary}\n"
        "原始任务描述：\n"
        f'"""\n{task_description}\n"""\n'
        "原始场景描述：\n"
        f'"""\n{scene_description}\n"""\n'
        "上一版分配：\n"
        f'"""\n{initial_response}\n"""\n'
        "现在只输出检查和反思过程，不要输出最终任务分配。"
    )


def create_leader_final_assignment_message(
    initial_response: str,
    review_response: str,
    task_description: str,
    scene_description: str,
    robot_ids: List[str],
) -> str:
    hints = _radxa_scene_hint_text(scene_description)
    structured_summary = _radxa_structured_scene_summary(scene_description)
    allowed = "、".join(robot_ids)
    return (
        "现在请根据你自己的检查和反思，重新输出最终任务分配。\n"
        "只输出最终任务分配行，不要输出反思过程、解释、编号、JSON 或 Markdown。\n"
        "你可以按自己的判断保留、调整、合并或省略目标；"
        "任意机器人都可以输出「无需执行」。\n"
        f"只能使用这些 robot_id：{allowed}。\n"
        f"{hints}\n"
        f"{structured_summary}\n"
        "原始任务描述：\n"
        f'"""\n{task_description}\n"""\n'
        "原始场景描述：\n"
        f'"""\n{scene_description}\n"""\n'
        "上一版分配：\n"
        f'"""\n{initial_response}\n"""\n'
        "你的检查和反思内容：\n"
        f'"""\n{review_response}\n"""\n'
        "请输出最终任务分配。"
        + _radxa_leader_output_constraints(robot_ids)
    )

def create_robot_prompt(
    robot_type,
    robot_key,
    capabilities,
    execute_code=True,
    llm_output_profile: Optional[str] = None,
):

    ROBOT_GROUP_DISCUSS_SYSTEM_PROMPT_TEMPLATE = (
        '你是一个名为 "{robot_key}" 的 "{robot_type}" 类型机器人智能体。'
        "你具有以下能力：\n\n"
        '"""\n{capabilities}\n"""\n\n'
        "重要提示：感知能力中的 'max_range' 仅用于传感器检测（如障碍物检测），不是导航距离的限制。"
        "你可以导航到任何可达位置，不受感知范围限制，只要路径规划提供了有效路线即可。"
        "导航使用预先计算的 NavMesh 路径，因此你不需要看到目的地也能到达。\n\n"
        "你将收到组长分配的一个子任务。如果没有需要你执行的任务，你会收到「无需执行」。"
        "你的任务是通过常识推理来判断自己是否能够完成所分配的任务。"
        "对于导航任务，只要目的地物理上可达（没有被墙壁阻挡），你就应该接受。\n"
        "重要：你的所有回复和推理必须使用简体中文。"
    )
    CODE_EXECUTION = (
        "你可以生成 Python 代码来进行数值可行性检查，但必须确保代码可执行，即所有变量在引用前必须显式定义。"
        "重要：代码仅用于数学计算（距离、角度等）来检查可行性。不要在代码中调用任何机器人动作函数（如 nav_to_position、pick、place 等）——那些是后续执行的动作，不是 Python 函数。"
        "你必须在代码开头自行定义所有变量。例如，如果需要机器人的当前位置，你必须从场景描述中提取并定义：current_pos = [0.0, 0.0, 0.0]。"
        "在考虑操控任务时，你只需关注中心点、半径、包围盒等参数。"
        "我会执行代码并将结果返回给你，以帮助你做出决策。"
        r"请将所有代码放在一个 ```python 和 ``` 包裹的代码块中。"
        r'你必须在代码中使用 print 以 "<变量名>: <值>" 的格式打印你想要了解的变量。'
        "记住：如需数学函数请 import math。在使用前定义每个变量！只能使用标准 Python（math 等），不能调用机器人动作！"
    )
    FORMAT_INSTRUCTION = (
        "最后，如果你认为分配给你的任务不合适，可以说明原因并提醒组长将任务分配给其他智能体，"
        r'格式为："{{no||<原因和建议>}}"。'
        r'如果你认为任务合适，请回复 "{{yes}}"。'
        r'如果分配给你的任务是「无需执行」，直接回复 "{{yes}}"。'
        r"回复示例：{{yes}}、{{no||我没有移动能力}}、{{no||物体超出了我的机械臂工作空间}}。"
    )
    RADXA_FORMAT_INSTRUCTION = ""
    if llm_output_profile == "radxa-local":
        FORMAT_INSTRUCTION = (
            "最后，请判断分配给你的任务是否符合你的能力和场景硬约束。"
            "如果任务合适，请回复 {{yes}}。"
            "如果分配给你的任务是「无需执行」，直接回复 {{yes}}。"
            "如果任务不合适，请回复 {{no||简短原因}}。"
        )
        RADXA_FORMAT_INSTRUCTION = (
            "\n本地 Radxa LLM 输出约束（必须遵守）：\n"
            "只输出最终判断，不要输出 JSON、Markdown、编号、解释或思考过程。\n"
            "如果可以执行，只输出 {{yes}}。\n"
            "如果不可以执行，只输出 {{no||原因}}，其中原因必须是简短中文。\n"
        )

    if execute_code:
        return ROBOT_GROUP_DISCUSS_SYSTEM_PROMPT_TEMPLATE.format(
            robot_type=robot_type,
            robot_key=robot_key,
            capabilities=capabilities) + CODE_EXECUTION + FORMAT_INSTRUCTION + RADXA_FORMAT_INSTRUCTION
    else:
        return ROBOT_GROUP_DISCUSS_SYSTEM_PROMPT_TEMPLATE.format(
            robot_type=robot_type,
            robot_key=robot_key,
            capabilities=capabilities) + FORMAT_INSTRUCTION + RADXA_FORMAT_INSTRUCTION

def create_robot_start_message(
    task_description,
    scene_description,
    compute_path: bool = False,
    llm_output_profile: Optional[str] = None,
):

    ROBOT_GROUP_DISCUSS_MESSAGE_TEMPLATE = (
        "你的任务是与其他智能体协作，完成以下分配给你的子任务：\n\n"
        '"""\n{task_description}\n"""\n\n'
        "场景描述如下：\n\n"
        '"""\n{scene_description}\n"""\n\n'
    )
    COMPUTE_PATH = (
        "请根据区域描述推断导航路径，并评估是否需要跨越不同地面。"
        "你应根据自身能力判断是否能成功完成任务。"
        "如果你要抓取或放置物体，请检查物体高度是否超出机器人的可达范围——如果工作空间类型为球形，需考虑距离中心点半径范围内的最大可达高度；如果为盒形，需考虑高度轴上的最大边界。为简便起见，你只需检查高度。"
        "注意：所有坐标的格式为 [x, z, y]，其中第二个坐标代表高度，第一个和第三个坐标代表水平位置。"
    )
    COMPUTE_SPACE = (
        "如果你要抓取或放置物体，在检查高度时，请确认物体高度是否超出机器人的可达范围——如果工作空间类型为球形，需考虑距离中心点半径范围内的最大可达高度；如果为盒形，需考虑高度轴上的最大边界。\n"
        "如果你要抓取或放置物体，在检查水平距离时，请确认物体与中心点之间的水平距离和高度差的对角线是否在球形工作空间的半径内；如果为盒形，请检查水平距离是否在包围盒 x-y 平面对角线范围内。\n"
        "为简便起见，你必须分别检查高度和水平距离。\n"
        "如果你需要检测物体，需要使用 hfov（从上到下的感知角度）、高度差、水平距离来检查物体是否在你的垂直视野范围内。\n"
        "注意：所有坐标的格式为 [x, z, y]，其中第二个坐标代表高度，第一个和第三个坐标代表水平位置。"
    )
    RADXA_REFLECTION = (
        "请只根据你的能力描述和场景中的硬约束判断该分配是否合理。"
        "如果分配给你的任务是「无需执行」，必须输出 {{yes}}，不要对「无需执行」进行能力否定。"
        "先核对上方能力描述：如果能力描述中写明具备移动、机械臂、传感器或对应能力，应承认这些能力存在。"
        "如果能力描述包含移动能力，应承认自己有移动能力。"
        "如果能力描述包含机械臂或可抓取能力，应承认自己有操控能力。"
        "分配给你的子任务本身就是明确任务描述，不要因为没有额外解释而拒绝。"
        "不得扩展判断场景中未提供的信息；"
        "如果任务符合你的能力和场景硬约束，必须输出 {{yes}}。"
        "如果你的理由认为任务合理、可执行或符合能力，必须输出 {{yes}}，禁止输出 {{no||任务合理}}。"
        "只有当场景明确写明该任务禁止你执行、或你的能力描述明确缺失所需能力时，才输出 {{no||简短原因}}。"
    )
    message = ROBOT_GROUP_DISCUSS_MESSAGE_TEMPLATE.format(
        task_description=task_description,
        scene_description=scene_description,
    )
    if llm_output_profile == "radxa-local":
        return message + RADXA_REFLECTION
    elif compute_path:
        return message + COMPUTE_PATH
    else:
        return message + COMPUTE_SPACE


NO_MANIPULATION = "没有显式的操控组件"
NO_MOBILITY = "提供的 URDF 中不包含特定的运动关节"
NO_PERCEPTION = "未知"


def get_text_capabilities(robot: dict):
    """机器人能力提取 summary 字段"""
    capabilities = ""
    static_capabilities = robot.get("capabilities")
    if isinstance(static_capabilities, list) and static_capabilities:
        capabilities += "能力列表:\n"
        for item in static_capabilities:
            capabilities += f"- {item}\n"
    if "mobility" in robot:
        mobility = json.dumps(robot["mobility"]["summary"])
        capabilities += f"移动能力: {mobility}\n"
    if "perception" in robot:
        perception = json.dumps(robot["perception"]["summary"])
        capabilities += f"感知能力: {perception}\n"
    if "manipulation" in robot:
        manipulation = json.dumps(robot["manipulation"]["summary"])
        capabilities += f"操控能力: {manipulation}\n"
    return capabilities


def get_full_capabilities(robot: dict):
    """机器人能力提取所有字段"""
    capabilities = "以下是描述该机器人能力的 Python 字典列表：\n"
    static_capabilities = robot.get("capabilities")
    if isinstance(static_capabilities, list) and static_capabilities:
        capabilities += f" - 能力列表: {json.dumps(static_capabilities, ensure_ascii=False)}\n"
    if "mobility" in robot:
        mobility = json.dumps(robot["mobility"])
        capabilities += f" - 移动能力: {mobility}\n"
    if "perception" in robot:
        perception = json.dumps(robot["perception"])
        capabilities += f" - 感知能力: {perception}\n"
    if "manipulation" in robot:
        manipulation = json.dumps(robot["manipulation"])
        capabilities += f" - 操控能力: {manipulation}\n"
    return capabilities

def parse_leader_response(text, allowed_robot_ids: Optional[List[str]] = None):
    """提取 leader 的任务分配结果"""
    allowed = set(allowed_robot_ids or [])
    text = text or ""
    # 标准路径：保留子任务描述里的中文标点，只以大括号块作为边界。
    matches = re.findall(r"\{\s*([A-Za-z0-9_-]+)\s*\|\|\s*(.*?)\s*\}", text, flags=re.S)
    if not matches:
        # Radxa 本地小模型有时把每条分配写成 JSON/列表里的 quoted string：
        # "m20_1||火源侦查任务", "m20_2||灭火器运送任务"
        matches = re.findall(r"[\"']\s*([A-Za-z0-9_-]+)\s*\|\|\s*([^\"']+?)\s*[\"']", text, flags=re.S)
    if not matches:
        # 最后兜底：没有大括号/引号时，从行里提取 ``id||task``。
        matches = re.findall(r"^\s*([A-Za-z0-9_-]+)\s*\|\|\s*(.+?)\s*$", text, flags=re.M)
    robot_tasks: Dict[str, str] = {}
    for robot_id, subtask_description in matches:
        robot_id = robot_id.strip().strip("{}[]()\"'` ")
        if allowed and robot_id not in allowed:
            continue
        task = subtask_description.strip().strip("{}[]()\"'` ,，;；")
        if not task:
            continue
        robot_tasks[robot_id] = task
    if allowed_robot_ids is not None:
        return {robot_id: robot_tasks[robot_id] for robot_id in allowed_robot_ids if robot_id in robot_tasks}
    return robot_tasks


def _is_clean_leader_assignment_text(text: str, robot_ids: List[str]) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != len(robot_ids):
        return False
    seen = set()
    for line in lines:
        match = re.fullmatch(r"\{\s*([A-Za-z0-9_-]+)\s*\|\|\s*[^{}]+?\s*\}", line)
        if not match:
            return False
        robot_id = match.group(1)
        if robot_id not in robot_ids or robot_id in seen:
            return False
        seen.add(robot_id)
    return seen == set(robot_ids)


def _leader_format_retry_prompt(robot_ids: List[str]) -> str:
    allowed = "、".join(robot_ids)
    return (
        "上一条回复的格式不符合要求。请只重发任务分配行，只输出任务分配本身，不要解释、寒暄、编号、Markdown、"
        "思考过程、JSON、代码块或任何额外文字。不要输出代码块，不要输出键值对，不要使用冒号、逗号或引号组织答案。\n"
        "每一行必须且只能是一条任务分配，格式为 {robot_id||子任务描述}。\n"
        f"每个 robot_id 必须且只能出现一次，只能使用这些 robot_id：{allowed}。\n"
        "目标名称必须从原始目标条件中逐字复制，不要创造新的目标或任务类型。"
        "任何不在原始目标条件列表中的词都不要作为任务名输出。"
    )


def _canonical_leader_assignment_text(text: str, robot_ids: List[str]) -> str:
    tasks = parse_leader_response(text, allowed_robot_ids=robot_ids)
    return "\n".join(f"{{{robot_id}||{tasks[robot_id]}}}" for robot_id in robot_ids if robot_id in tasks)


def _chat_leader_with_format_retry(
    leader,
    prompt: str,
    robot_ids: List[str],
    *,
    llm_output_profile: Optional[str],
) -> str:
    response = leader.chat(prompt)
    if llm_output_profile != "radxa-local":
        return response
    if _is_clean_leader_assignment_text(response, robot_ids):
        return response
    retry_response = leader.chat(_leader_format_retry_prompt(robot_ids))
    if _is_clean_leader_assignment_text(retry_response, robot_ids):
        return retry_response
    canonical = _canonical_leader_assignment_text(retry_response, robot_ids)
    if canonical:
        preview = (retry_response or "").replace("\n", "\\n")[:240]
        print(
            "[WARNING] Radxa leader 回复仍含格式噪声，已仅规范化协议外壳；"
            f"原始回复片段: {preview}"
        )
        return canonical
    return retry_response


def _parse_leader_response_or_raise(
    text: str,
    robot_ids: List[str],
    *,
    context: str,
) -> Dict[str, str]:
    robot_tasks = parse_leader_response(text, allowed_robot_ids=robot_ids)
    if not robot_tasks:
        preview = (text or "").replace("\n", " ")[:240]
        raise LeaderResponseFormatError(
            f"{context}: leader 回复未解析出任何有效机器人任务；"
            f"期望格式为 {{robot_id||子任务描述}}，已知 robot_id={robot_ids}。"
            f" 原始回复片段: {preview}"
        )
    missing = [robot_id for robot_id in robot_ids if robot_id not in robot_tasks]
    if missing:
        print(f"[WARNING] leader 回复缺少机器人任务 {missing}，已补为「无需执行」。")
        for robot_id in missing:
            robot_tasks[robot_id] = "无需执行"
    return {robot_id: robot_tasks[robot_id] for robot_id in robot_ids}


def _assistant_stream_text(text: str) -> str:
    text = text or ""
    marker = "<｜Assistant｜>"
    if marker in text:
        return text.rsplit(marker, 1)[-1]
    if "<｜User｜>" in text or "<｜begin" in text or "输出示例：" in text:
        return ""
    return text


def create_leader_stream_stop_checker(
    robot_ids: List[str],
):
    def _checker(text: str) -> bool:
        tasks = parse_leader_response(_assistant_stream_text(text), allowed_robot_ids=robot_ids)
        return len(tasks) >= len(robot_ids)

    return _checker


def create_agent_stream_stop_checker():
    def _checker(text: str) -> bool:
        return bool(re.search(r"\{\{\s*(?:yes|no)(?:\|\|.*?)?\s*\}\}", _assistant_stream_text(text), flags=re.S))

    return _checker


def parse_agent_response(text):
    """解析机器人回复"""
    pattern = r"\{(yes|no)(?:\|\|(.*?))?\}"
    matches = re.findall(pattern, text)
    if len(matches) == 0:
        print("未在智能体回复中找到匹配: ", text)
        return "no", text
    response, reason = matches[0]
    if response == "yes":
        reason = None
    elif response == "no":
        reason = (
            reason.strip() if reason else ""
        )
        positive_markers = (
            "任务合理",
            "可以执行",
            "可执行",
            "符合能力",
            "符合场景",
            "符合硬约束",
            "具备",
        )
        negative_markers = (
            "不具备",
            "没有",
            "无法",
            "不能",
            "禁止",
            "缺少",
            "不适合",
            "超出",
        )
        if (
            reason
            and any(marker in reason for marker in positive_markers)
            and not any(marker in reason for marker in negative_markers)
        ):
            return "yes", None
    return response, reason


DISCUSSION_TOOLS = []

ROBOT_DESCRIPTION = {
    # 工厂场景
    "CarterOrsusRobot": "Carter 差速驱动机器人，搭载 Orsus 传感器（激光雷达、通过 ROS2 的里程计），用于室内导航与建图。",
    "CarterRobot": "Carter 是一款差速驱动轮式机器人，具备 RGBD / 激光雷达感知能力。",
    "M20FrankaRobot": "M20 轮足四足机器人，背部搭载 Franka Panda 7自由度机械臂，适用于复杂地形操控任务。",
    "M20Robot": "M20 是一款轮足混合四足机器人，结合轮式和腿式运动，适应复杂地形。",
}

@dataclass
class AgentArguments:
    """任务分配结果的数据结构"""
    robot_id: str
    robot_type: str
    task_description: str
    subtask_description: str
    chat_history: list[dict[str, str]]

def group_discussion(
    robot_resume: dict,
    scene_description: str,
    task_description: str,
    save_chat_history=True,
    save_chat_history_dir="",
    episode_id=-1,
    should_group_discussion: bool = True,
    should_agent_reflection: bool = True,
    should_robot_resume: bool = True,
    should_numerical: bool = True,
    max_discussion_rounds = 3,
    parallel_reflection: bool = False,
    llm_model: Optional[str] = None,
    llm_base_url: Optional[str] = None,
    llm_api_key_env: Optional[str] = None,
    llm_api_key_default: Optional[str] = None,
    llm_stream: Optional[bool] = None,
    llm_temperature: Optional[float] = None,
    llm_top_p: Optional[float] = None,
    llm_temperature_param: Optional[str] = None,
    llm_output_profile: Optional[str] = None,
    llm_max_tokens: Optional[int] = None,
    llm_leader_self_reflection: bool = False,
) -> dict[str, AgentArguments]:

    ### 0. 是否保存聊天记录
    episode_save_dir = None
    if save_chat_history:
        episode_save_dir = os.path.join(save_chat_history_dir, str(episode_id))
        if not os.path.exists(episode_save_dir):
            os.makedirs(episode_save_dir)

    ### 1. 获取机器人信息
    compute_path = "regions_description" in scene_description
    robot_resume = json.loads(robot_resume)
    robot_resume_prompt = ""
    capabilities_list = {}

    for robot_key in robot_resume:
        resume = robot_resume[robot_key]

        if should_numerical and should_robot_resume:
            capabilities_list[robot_key] = get_full_capabilities(resume)
        elif should_robot_resume:
            capabilities_list[robot_key] = get_text_capabilities(resume)
        else:
            capabilities_list[robot_key] = ROBOT_DESCRIPTION[resume['robot_type']]

        robot_resume_prompt += ROBOT_RESUME_TEMPLATE.format(
            robot_key=robot_key,
            robot_type=resume["robot_type"],
            capabilities=capabilities_list[robot_key],
        )
    robot_ids = list(robot_resume.keys())

    ### 2. 如果不进行讨论，返回空子任务描述
    if not should_group_discussion:
        results = {}
        for robot_key in robot_resume:
            results[robot_key] = AgentArguments(
                robot_id=robot_key,
                robot_type=robot_resume[robot_key]["robot_type"],
                task_description=task_description,
                subtask_description="",
                chat_history=None,
            )
        return results

    ### 3. 创建组长（Leader）智能体
    leader_prompt = create_leader_prompt(
        robot_resume_prompt,
        robot_ids=robot_ids,
        llm_output_profile=llm_output_profile,
    )

    _llm_kw: Dict[str, Any] = {}
    if llm_model is not None:
        _llm_kw["model"] = llm_model
    if llm_base_url is not None:
        _llm_kw["base_url"] = llm_base_url
    if llm_api_key_env is not None:
        _llm_kw["api_key_env"] = llm_api_key_env
    if llm_api_key_default is not None:
        _llm_kw["api_key_default"] = llm_api_key_default
    if llm_stream is not None:
        _llm_kw["stream"] = llm_stream
    if llm_temperature is not None:
        _llm_kw["temperature"] = llm_temperature
    if llm_top_p is not None:
        _llm_kw["top_p"] = llm_top_p
    if llm_temperature_param is not None:
        _llm_kw["temperature_param"] = llm_temperature_param
    if llm_max_tokens is not None:
        _llm_kw["max_tokens"] = llm_max_tokens

    leader_llm_kw = dict(_llm_kw)
    if llm_output_profile == "radxa-local" and llm_stream:
        leader_llm_kw["stream_stop_checker"] = create_leader_stream_stop_checker(
            robot_ids,
        )
    agent_llm_kw = dict(_llm_kw)
    if llm_output_profile == "radxa-local" and llm_stream:
        agent_llm_kw["stream_stop_checker"] = create_agent_stream_stop_checker()

    leader = OpenAIModel(
        leader_prompt,
        DISCUSSION_TOOLS,
        discussion_stage=True,
        code_execution=False,
        enable_logging=save_chat_history,
        logging_file = os.path.join(episode_save_dir, "leader_group_chat_history.json") if episode_save_dir else None,
        agent_name="leader",
        **leader_llm_kw,
    )

    ### 4. 创建机器人智能体
    execute_code = should_numerical and should_robot_resume

    agents: Dict[str, OpenAIModel] = {}
    for robot_key in robot_resume:
        robot_prompt = create_robot_prompt(
            robot_resume[robot_key]["robot_type"],
            robot_key,
            capabilities_list[robot_key],
            execute_code,
            llm_output_profile=llm_output_profile,
        )
        agents[robot_key] = OpenAIModel(
            robot_prompt,
            DISCUSSION_TOOLS,
            discussion_stage=True,
            code_execution=execute_code,
            enable_logging=save_chat_history,
            logging_file = os.path.join(episode_save_dir, f"{robot_key}_group_chat_history.json") if episode_save_dir else None,
            agent_name=robot_resume[robot_key]["robot_type"],
            **agent_llm_kw,
        )

    ### 5. 组长接收任务和场景，初始分配子任务
    leader_start_message = create_leader_start_message(
        task_description=task_description,
        scene_description=scene_description,
        llm_output_profile=llm_output_profile,
    )
    response = _chat_leader_with_format_retry(
        leader,
        leader_start_message,
        robot_ids,
        llm_output_profile=llm_output_profile,
    )
    if llm_output_profile == "radxa-local" and llm_leader_self_reflection:
        review_response = leader.chat(
            create_leader_assignment_review_message(
                response,
                task_description,
                scene_description,
                robot_ids,
            )
        )
        response = _chat_leader_with_format_retry(
            leader,
            create_leader_final_assignment_message(
                response,
                review_response,
                task_description,
                scene_description,
                robot_ids,
            ),
            robot_ids,
            llm_output_profile=llm_output_profile,
        )
    robot_tasks = _parse_leader_response_or_raise(
        response,
        robot_ids,
        context="初始任务分配",
    )
    print("=============== 场景描述 ==============")
    print(scene_description)
    print("=============== 任务描述 ==============")
    print(task_description)
    print("=============== 组长回复 ==============")
    print(response)
    print("===========================================")

    agent_response = {}
    effective_parallel_reflection = (
        parallel_reflection and llm_output_profile != "radxa-local"
    )
    if not should_agent_reflection:
        results = {}
        for agent in agents:
            results[agent] = AgentArguments(
                robot_id=agent,
                robot_type=robot_resume[agent]["robot_type"],
                task_description=task_description,
                subtask_description=robot_tasks[agent],
                chat_history=agents[agent].chat_history,
            )
        return results

    ### 6. 机器人智能体对分配的任务进行反思
    def _reflect_single(robot_id):
        """单个机器人反思（用于串行和并行模式）。"""
        robot_model = agents[robot_id]
        robot_start_message = create_robot_start_message(
            task_description=robot_tasks[robot_id],
            scene_description=scene_description,
            compute_path=compute_path,
            llm_output_profile=llm_output_profile,
        )
        resp = robot_model.chat(robot_start_message)
        parsed = parse_agent_response(resp)
        print(f"=============== 机器人回复 ({robot_id}) ==============")
        print(f"机器人 {robot_id} 回复: {resp}")
        print("===========================================")
        return robot_id, parsed

    if effective_parallel_reflection:
        with ThreadPoolExecutor(max_workers=min(4, len(robot_tasks))) as pool:
            futures = {pool.submit(_reflect_single, rid): rid for rid in robot_tasks}
            for f in as_completed(futures):
                rid, parsed = f.result()
                agent_response[rid] = parsed
    else:
        for robot_id in robot_tasks:
            rid, parsed = _reflect_single(robot_id)
            agent_response[rid] = parsed

    ### 7. 组长根据反馈修正任务分配
    for _ in range(max_discussion_rounds):
        all_yes = True
        prompt = "各机器人智能体的反馈如下：\n"
        for robot_id, (response, reason) in agent_response.items():
            if response == "no":
                prompt += f"机器人 {robot_id} 回复: {response}，原因: {reason}\n"
                all_yes = False
        if all_yes:
            break
        prompt += "请根据以上反馈修改任务并重新分配子任务。"
        prompt += (
            "确保所有机器人完成各自子任务后，总体目标条件全部满足。"
            "你应将某些智能体反馈不可行的任务重新分配给其他智能体。"
            r"每个智能体仍需按照以下格式描述：{robot_id||子任务描述}\n"
        )
        if llm_output_profile == "radxa-local":
            prompt += _radxa_leader_output_constraints(robot_ids)

        response = _chat_leader_with_format_retry(
            leader,
            prompt,
            robot_ids,
            llm_output_profile=llm_output_profile,
        )
        robot_tasks = _parse_leader_response_or_raise(
            response,
            robot_ids,
            context="反馈后任务重分配",
        )

        print("=============== 组长回复 ==============")
        print(response)
        print("===========================================")

        if effective_parallel_reflection:
            with ThreadPoolExecutor(max_workers=min(4, len(robot_tasks))) as pool:
                futures = {pool.submit(_reflect_single, rid): rid for rid in robot_tasks}
                for f in as_completed(futures):
                    rid, parsed = f.result()
                    agent_response[rid] = parsed
        else:
            for robot_id in robot_tasks:
                rid, parsed = _reflect_single(robot_id)
                agent_response[rid] = parsed

    results = {}
    for agent in agents:
        results[agent] = AgentArguments(
            robot_id=agent,
            robot_type=robot_resume[agent]["robot_type"],
            task_description=task_description,
            subtask_description=robot_tasks[agent],
            chat_history=agents[agent].chat_history,
        )
    leader_tokens = leader.token_usage
    robot_tokens = sum([agent.token_usage for agent in agents.values()])
    total_tokens = leader_tokens + robot_tokens
    print("=============== 任务分配结果 ==============")
    print(robot_tasks)
    print("=============== Token 用量统计 ========================")
    print(f"组长 Token 用量: {leader_tokens}")
    print(f"机器人 Token 用量: {robot_tokens}")
    print(f"总计 Token 用量: {total_tokens}")
    print("========================================================")

    return results
