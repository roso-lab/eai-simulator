from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import catalog

ROBOT_KEYS = catalog.ROBOT_KEYS
ORSUS_SUPPORTED_ROBOTS = frozenset(catalog.attachment_entry("orsus").supported_robots)
REALSENSE_D455_SUPPORTED_ROBOTS = frozenset(catalog.attachment_entry("realsense_d455").supported_robots)
NAVIGATION_IO_SUPPORTED_ROBOTS = frozenset(
    catalog.tool_catalog()["navigation_io"].supported_robots
)
KEYBOARD_SUPPORTED_ROBOTS = frozenset(catalog.tool_catalog()["keyboard"].supported_robots)
UR5_SUPPORTED_ROBOTS = frozenset(catalog.attachment_entry("ur5").supported_robots)
Z1_SUPPORTED_ROBOTS = frozenset(catalog.attachment_entry("z1").supported_robots)
LIDAR_SUPPORTED_ROBOTS = frozenset(catalog.attachment_entry("lidar").supported_robots)
CAMERA_SUPPORTED_ROBOTS = frozenset(catalog.tool_catalog()["camera"].supported_robots)
TERMINAL_CONTROLLER_STEP = 10
SCENE_CHOICES = catalog.SCENE_CHOICES
ROBOT_LABELS = catalog.ROBOT_LABELS
_TERMINAL_RULE = "-" * 72
_TERMINAL_STEP_COUNT = 5


def _print_terminal_banner(print_func) -> None:
    print_func("")
    print_func("=" * 72)
    print_func("  EAI ENV DIY  |  终端快速配置")
    print_func("  Scenes  >  Robots  >  Payloads  >  Tools  >  Controllers")
    print_func("=" * 72)
    print_func("  Enter 使用默认值或跳过可选项，b 返回上一步")


def _print_terminal_step(print_func, number: int, title: str, detail: str) -> None:
    print_func(f"\n[{number}/{_TERMINAL_STEP_COUNT}] {title}")
    print_func(_TERMINAL_RULE)
    print_func(f"  {detail}")


def _print_terminal_subsection(print_func, title: str) -> None:
    print_func(f"\n  {title}")
    print_func("  " + "-" * 36)


def _print_choice_grid(items: list[str] | tuple[str, ...], *, print_func) -> None:
    if not items:
        return
    row_count = (len(items) + 1) // 2
    column_width = max(len(item) for item in items) + 4
    for row in range(row_count):
        cells = []
        for index in (row, row + row_count):
            if index < len(items):
                cells.append(items[index].ljust(column_width))
        print_func("  " + "".join(cells).rstrip())


def _print_selection_summary(
    scene_key: str,
    robots: list[RobotSelection],
    *,
    print_func,
) -> None:
    scene_label = next(
        (label for key, label in SCENE_CHOICES if key == scene_key),
        scene_key,
    )
    print_func("\n配置预览")
    print_func(_TERMINAL_RULE)
    print_func(f"  Scene    {scene_label} ({scene_key})")
    print_func(f"  Robots   {len(robots)}")
    for index, robot in enumerate(robots, start=1):
        payloads = ", ".join(
            catalog.tool_label(item.type)
            if catalog.attachment_entry(item.type).category == "tool"
            else item.type
            for item in robot.attachments
        ) or "none"
        print_func(f"    {index:>2}. {robot.type}_{index}")
        print_func(f"        Payloads    {payloads}")
        print_func(f"        Controller  {robot.controller.cfg or 'none'}")


@dataclass(frozen=True)
class ControllerChoice:
    mode: str = "default"
    cfg: str | None = None


@dataclass(frozen=True)
class AttachmentSelection:
    type: str
    controller: ControllerChoice | None = None


@dataclass(frozen=True)
class RobotSelection:
    type: str
    controller: ControllerChoice
    visual: dict[str, float] = field(default_factory=dict)
    attachments: tuple[AttachmentSelection, ...] = ()
    spawn_pose: dict[str, tuple[float, ...]] | None = None


@dataclass(frozen=True)
class InteractiveSelection:
    scene_key: str
    robots: tuple[RobotSelection, ...]
    task_name: str | None = None


def is_back_token(value: str) -> bool:
    return value.strip().lower() in {"b", "back"}


def parse_robot_digit_selection(raw: str, *, robot_keys: tuple[str, ...] = ROBOT_KEYS) -> tuple[str, ...]:
    text = raw.strip()
    if not text:
        raise ValueError("Please enter at least one robot number.")
    keys_by_name = {key.lower(): key for key in robot_keys}
    if text.lower() in keys_by_name:
        return (keys_by_name[text.lower()],)
    selected: list[str] = []
    for token in text.split():
        if not token.isdigit():
            raise ValueError(f"Invalid robot number: {token}")
        index = int(token) - 1
        if index < 0 or index >= len(robot_keys):
            raise ValueError(f"Robot number out of range: {token}")
        selected.append(robot_keys[index])
    if not selected:
        raise ValueError("Please enter at least one robot number.")
    return tuple(selected)


def controller_cfg_names() -> tuple[str, ...]:
    return catalog.controller_cfg_names()


def default_controller_cfg(robot_type: str) -> str | None:
    return catalog.default_controller_cfg(robot_type)


def attachment_supported(robot_type: str, attachment_type: str) -> bool:
    return catalog.attachment_supported(robot_type, attachment_type)


def choose_terminal_interactive_selection(
    *,
    initial_selection: InteractiveSelection | None = None,
    allow_back_from_first: bool = False,
    start_step: int = 0,
    input_func=input,
    print_func=print,
) -> InteractiveSelection | None:
    _print_terminal_banner(print_func)
    step = start_step
    scene_key: str | None = None
    robots: list[RobotSelection] = []
    before_controller_override: list[RobotSelection] = []
    if initial_selection is not None:
        scene_key = initial_selection.scene_key
        robots = list(initial_selection.robots)
        before_controller_override = list(initial_selection.robots)

    while True:
        if step == 0:
            result = _choose_scene(
                allow_back=allow_back_from_first,
                input_func=input_func,
                print_func=print_func,
            )
            if result is None:
                return None
            scene_key = result
            step = 1
            continue

        if step == 1:
            robot_keys = _choose_robot_digits(input_func=input_func, print_func=print_func)
            if robot_keys is None:
                step = 0
                continue
            robots = [
                RobotSelection(
                    type=key,
                    controller=ControllerChoice("default", default_controller_cfg(key)),
                    visual={},
                    attachments=(),
                )
                for key in robot_keys
            ]
            step = 2
            continue

        if step == 2:
            _print_terminal_step(
                print_func,
                3,
                "Payloads / 载荷",
                "先配置机械臂，再配置传感器",
            )
            _print_terminal_subsection(print_func, "Manipulators / 机械臂")
            updated = _choose_attachments(
                "UR5",
                "ur5",
                robots,
                input_func=input_func,
                print_func=print_func,
            )
            if updated is None:
                step = 1
                continue
            robots = updated
            step = 3
            continue

        if step == 3:
            updated = _choose_attachments(
                "Z1",
                "z1",
                robots,
                input_func=input_func,
                print_func=print_func,
            )
            if updated is None:
                step = 2
                continue
            robots = updated
            before_controller_override = list(robots)
            step = 4
            continue

        if step == 4:
            _print_terminal_subsection(print_func, "Sensors / 传感器")
            updated = _choose_attachments(
                "Orsus",
                "orsus",
                robots,
                input_func=input_func,
                print_func=print_func,
            )
            if updated is None:
                step = 3
                continue
            robots = updated
            step = 5
            continue

        if step == 5:
            updated = _choose_attachments(
                "RealSense D455",
                "realsense_d455",
                robots,
                input_func=input_func,
                print_func=print_func,
            )
            if updated is None:
                step = 4
                continue
            robots = updated
            step = 6
            continue

        if step == 6:
            updated = _choose_attachments(
                "LiDAR",
                "lidar",
                robots,
                input_func=input_func,
                print_func=print_func,
            )
            if updated is None:
                step = 5
                continue
            robots = updated
            step = 7
            continue

        if step == 7:
            _print_terminal_step(
                print_func,
                4,
                "Tools / 工具",
                "为选中的机器人启用外部控制工具",
            )
            updated = _choose_attachments(
                catalog.tool_label("navigation_io"),
                "navigation_io",
                robots,
                input_func=input_func,
                print_func=print_func,
            )
            if updated is None:
                step = 6
                continue
            robots = updated
            step = 8
            continue

        if step == 8:
            updated = _choose_attachments(
                "Camera tool",
                "camera",
                robots,
                input_func=input_func,
                print_func=print_func,
            )
            if updated is None:
                step = 7
                continue
            robots = updated
            step = 9
            continue

        if step == 9:
            updated = _choose_attachments(
                "Keyboard tool",
                "keyboard",
                robots,
                input_func=input_func,
                print_func=print_func,
            )
            if updated is None:
                step = 9
                continue
            robots = updated
            before_controller_override = list(robots)
            step = TERMINAL_CONTROLLER_STEP
            continue

        _print_terminal_step(
            print_func,
            5,
            "Controllers / 控制器",
            "保留默认 cfg，或按对象单独修改",
        )
        updated = _maybe_override_controllers(
            before_controller_override,
            input_func=input_func,
            print_func=print_func,
        )
        if updated is None:
            step = 9
            continue
        if scene_key is None:
            raise RuntimeError("Scene was not selected.")
        _print_selection_summary(scene_key, updated, print_func=print_func)
        return InteractiveSelection(scene_key=scene_key, robots=tuple(updated))


def _choose_scene(*, allow_back: bool, input_func, print_func) -> str | None:
    _print_terminal_step(print_func, 1, "Scenes / 场景", "选择仿真环境")
    options = tuple(
        f"{index:>2}. {label}"
        for index, (_key, label) in enumerate(SCENE_CHOICES, start=1)
    )
    _print_choice_grid(options, print_func=print_func)
    while True:
        raw = input_func(f"\n选择场景 [1-{len(SCENE_CHOICES)}] (默认 1): ").strip()
        if allow_back and is_back_token(raw):
            return None
        if not raw:
            return SCENE_CHOICES[0][0]
        try:
            value = int(raw)
        except ValueError:
            print_func("  ! 请输入数字编号。")
            continue
        if 1 <= value <= len(SCENE_CHOICES):
            return SCENE_CHOICES[value - 1][0]
        print_func("  ! 编号超出范围。")


def _choose_robot_digits(*, input_func, print_func) -> tuple[str, ...] | None:
    _print_terminal_step(
        print_func,
        2,
        "Robots / 机器人",
        "可多选，使用空格分隔编号",
    )
    options = tuple(
        f"{index:>2}. {ROBOT_LABELS.get(key, key)}"
        for index, key in enumerate(ROBOT_KEYS, start=1)
    )
    _print_choice_grid(options, print_func=print_func)
    while True:
        raw = input_func("\n选择机器人 (例如 1 11，默认 1): ").strip()
        if not raw:
            raw = "1"
        if is_back_token(raw):
            return None
        try:
            return parse_robot_digit_selection(raw)
        except ValueError as exc:
            print_func(f"  ! 输入无效: {exc}")


def _choose_attachments(
    label: str,
    attachment_type: str,
    robots: list[RobotSelection],
    *,
    input_func,
    print_func,
) -> list[RobotSelection] | None:
    builtin_sensor_notes = [
        (index, robot, _builtin_sensor_note(robot.type, attachment_type))
        for index, robot in enumerate(robots, start=1)
        if catalog.builtin_sensor_capabilities(robot.type, attachment_type)
    ]
    candidates = [
        (index, robot)
        for index, robot in enumerate(robots, start=1)
        if attachment_supported(robot.type, attachment_type)
        and _attachment_combination_supported(robot, attachment_type)
        and not (robot.type in {"iris", "pegasus"} and attachment_type == "lidar")
        and not (
            attachment_type == "camera"
            and robot.type not in catalog.BUILTIN_CAMERA_ROBOTS
            and not any(item.type in {"orsus", "realsense_d455"} for item in robot.attachments)
        )
        and not (
            attachment_type in {"ur5", "z1"}
            and any(item.type in {"ur5", "z1"} for item in robot.attachments)
        )
        and not (
            attachment_type in {"orsus", "lidar"}
            and any(
                item.type in {"orsus", "lidar"} and item.type != attachment_type
                for item in robot.attachments
            )
        )
    ]
    _print_terminal_subsection(print_func, label)
    for index, robot, note in builtin_sensor_notes:
        robot_name = f"{robot.type}_{index}"
        robot_label = ROBOT_LABELS.get(robot.type, robot.type)
        print_func(f"    [--] {robot_name:<18} {robot_label} · {note}")
    if not candidates:
        if builtin_sensor_notes:
            print_func("    所选无人机已具备对应能力，无需重复挂载。")
        else:
            print_func("    无兼容机器人，已跳过。")
        return robots
    for index, robot in candidates:
        robot_name = f"{robot.type}_{index}"
        robot_label = ROBOT_LABELS.get(robot.type, robot.type)
        print_func(f"    [{index:>2}] {robot_name:<18} {robot_label}")
    while True:
        raw = input_func(f"  装配 {label} 到 (空为跳过): ").strip()
        if not raw:
            return robots
        if is_back_token(raw):
            return None
        try:
            selected = _parse_attachment_indices(raw)
        except ValueError:
            print_func("    ! 请输入编号，多个编号用空格或逗号分隔。")
            continue
        valid = {index for index, _robot in candidates}
        if not selected <= valid:
            print_func("    ! 编号包含不可装配的机器人。")
            continue
        updated = []
        for index, robot in enumerate(robots, start=1):
            attachments = list(robot.attachments)
            if index in selected and not any(item.type == attachment_type for item in attachments):
                controller_cfg = {"ur5": "UR5_IK_CFG", "z1": "Z1_IK_CFG"}.get(attachment_type)
                controller = ControllerChoice("default", controller_cfg) if controller_cfg else None
                attachments.append(AttachmentSelection(attachment_type, controller))
            updated.append(
                RobotSelection(robot.type, robot.controller, robot.visual, tuple(attachments), robot.spawn_pose)
            )
        selected_names = [
            f"{robot.type}_{index}"
            for index, robot in enumerate(robots, start=1)
            if index in selected
        ]
        print_func(f"    已选择: {', '.join(selected_names)}")
        return updated


def _builtin_sensor_note(robot_type: str, attachment_type: str) -> str:
    labels = {"camera": "相机", "lidar": "LiDAR"}
    capabilities = catalog.builtin_sensor_capabilities(robot_type, attachment_type)
    return "已内置" + "与".join(labels[item] for item in capabilities)


def _attachment_combination_supported(
    robot: RobotSelection,
    attachment_type: str,
) -> bool:
    try:
        catalog.validate_attachment_types(
            robot.type,
            [item.type for item in robot.attachments] + [attachment_type],
        )
    except ValueError:
        return False
    return True


def _maybe_override_controllers(
    robots: list[RobotSelection],
    *,
    input_func,
    print_func,
) -> list[RobotSelection] | None:
    should_override = _ask_yes_no_or_back(
        "是否修改机器人或机械臂 controller cfg",
        default=False,
        input_func=input_func,
        print_func=print_func,
    )
    if should_override is None:
        return None
    if not should_override:
        return robots

    _print_terminal_subsection(print_func, "可用 controller cfg")
    cfg_options = tuple(f"- {name}" for name in controller_cfg_names())
    _print_choice_grid(cfg_options, print_func=print_func)
    updated = list(robots)
    while True:
        options: list[tuple[str, int, str | None]] = []
        _print_terminal_subsection(print_func, "可修改的对象")
        display_index = 1
        for robot_index, robot in enumerate(updated):
            print_func(
                f"    [{display_index:>2}] {robot.type}_{robot_index + 1} base "
                f"({robot.controller.cfg})"
            )
            options.append(("robot", robot_index, None))
            display_index += 1
            for attachment in robot.attachments:
                if attachment.controller is not None:
                    print_func(
                        f"    [{display_index:>2}] {robot.type}_{robot_index + 1} "
                        f"{attachment.type} ({attachment.controller.cfg})"
                    )
                    options.append(("attachment", robot_index, attachment.type))
                    display_index += 1
        raw = input_func("输入要修改的对象编号，空为完成: ").strip()
        if not raw:
            return updated
        if is_back_token(raw):
            return None
        try:
            target = int(raw)
        except ValueError:
            print_func("  ! 请输入数字编号。")
            continue
        if not (1 <= target <= len(options)):
            print_func("  ! 编号超出范围。")
            continue
        cfg_name = input_func("输入新的 cfg 名称: ").strip()
        if is_back_token(cfg_name):
            continue
        if cfg_name not in controller_cfg_names():
            print_func(f"  ! 未知 cfg: {cfg_name}")
            continue
        kind, robot_index, attachment_type = options[target - 1]
        robot = updated[robot_index]
        if kind == "robot":
            updated[robot_index] = RobotSelection(
                robot.type,
                ControllerChoice("manual", cfg_name),
                robot.visual,
                robot.attachments,
                robot.spawn_pose,
            )
            continue
        attachments = []
        for attachment in robot.attachments:
            if attachment.type == attachment_type:
                attachments.append(AttachmentSelection(attachment.type, ControllerChoice("manual", cfg_name)))
            else:
                attachments.append(attachment)
        updated[robot_index] = RobotSelection(
            robot.type,
            robot.controller,
            robot.visual,
            tuple(attachments),
            robot.spawn_pose,
        )


def _parse_attachment_indices(raw: str) -> set[int]:
    text = raw.strip()
    if not text:
        return set()
    if "," in text or " " in text:
        return {int(item) for item in text.replace(",", " ").split()}
    return {int(char) for char in text}


def _ask_yes_no_or_back(prompt: str, *, default: bool, input_func, print_func) -> bool | None:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input_func(f"{prompt} [{suffix}]: ").strip().lower()
        if is_back_token(raw):
            return None
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print_func("  ! 请输入 y、n 或 b。")


def interactive_selection_to_dict(selection: InteractiveSelection) -> dict[str, Any]:
    data = asdict(selection)
    robots = []
    for robot in data["robots"]:
        normalized = {
            "type": robot["type"],
            "controller": _controller_to_dict(robot["controller"]),
            "visual": robot.get("visual") or {},
            "attachments": [
                {
                    "type": attachment["type"],
                    "controller": _controller_to_dict(attachment.get("controller")),
                }
                for attachment in robot.get("attachments", [])
            ],
        }
        if robot.get("spawn_pose") is not None:
            normalized["spawn_pose"] = catalog.spawn_pose_to_dict(robot["spawn_pose"])
        robots.append(normalized)
    data["robots"] = robots
    return data


def interactive_selection_from_dict(data: dict[str, Any]) -> InteractiveSelection:
    scene_key = str(data["scene_key"]).strip().lower()
    if scene_key not in {key for key, _label in SCENE_CHOICES}:
        raise ValueError(f"Unknown scene '{scene_key}'.")

    robots = []
    for robot in data.get("robots", []):
        robot_type = _canonical_robot_type(str(robot["type"]))
        controller_data = robot.get("controller")
        controller = _controller_from_dict(
            controller_data,
            default_cfg=default_controller_cfg(robot_type),
        )
        attachments = []
        attachment_types = catalog.validate_attachment_types(
            robot_type,
            [str(attachment.get("type", "")) for attachment in robot.get("attachments", [])],
        )
        attachment_data_by_type = {
            str(attachment.get("type", "")).strip().lower(): attachment
            for attachment in robot.get("attachments", [])
        }
        for attachment_type in attachment_types:
            attachment = attachment_data_by_type[attachment_type]
            attachment_controller = None
            if attachment_type in {"ur5", "z1"}:
                attachment_controller = _controller_from_dict(
                    attachment.get("controller"),
                    default_cfg="UR5_IK_CFG" if attachment_type == "ur5" else "Z1_IK_CFG",
                )
            attachments.append(AttachmentSelection(attachment_type, attachment_controller))
        robots.append(
            RobotSelection(
                type=robot_type,
                controller=controller,
                visual={
                    "x": float((robot.get("visual") or {}).get("x", 0.0)),
                    "y": float((robot.get("visual") or {}).get("y", 0.0)),
                },
                attachments=tuple(attachments),
                spawn_pose=_spawn_pose_from_dict(robot.get("spawn_pose")),
            )
        )
    return InteractiveSelection(
        scene_key=scene_key,
        robots=tuple(robots),
        task_name=data.get("task_name"),
    )


def _controller_from_dict(data: Any, *, default_cfg: str | None) -> ControllerChoice:
    if not data:
        return ControllerChoice("default", default_cfg)
    mode = str(data.get("mode", "default"))
    cfg = data.get("cfg")
    if mode == "manual":
        return ControllerChoice("manual", str(cfg or default_cfg or "manual"))
    return ControllerChoice("default", str(cfg or default_cfg or ""))


def _spawn_pose_from_dict(data: Any) -> dict[str, tuple[float, ...]] | None:
    return catalog.normalize_spawn_pose(data)


def _controller_to_dict(choice: Any) -> dict[str, str] | None:
    if choice is None:
        return None
    if isinstance(choice, dict):
        mode = str(choice.get("mode", "default"))
        cfg = choice.get("cfg")
    else:
        mode = choice.mode
        cfg = choice.cfg
    if mode == "manual":
        return {"mode": "manual", "cfg": str(cfg or "manual")}
    return {"mode": "default", "cfg": str(cfg or "")}


def _canonical_robot_type(robot_type: str) -> str:
    return catalog.canonical_robot_type(robot_type)
