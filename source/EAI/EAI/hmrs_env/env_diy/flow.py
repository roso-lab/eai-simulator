from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import catalog

ROBOT_KEYS = catalog.ROBOT_KEYS
GSHUB_SUPPORTED_ROBOTS = frozenset(catalog.attachment_entry("gshub").supported_robots)
ROS_TOOL_SUPPORTED_ROBOTS = frozenset(catalog.tool_catalog()["ros"].supported_robots)
KEYBOARD_SUPPORTED_ROBOTS = frozenset(catalog.tool_catalog()["keyboard"].supported_robots)
UR5_SUPPORTED_ROBOTS = frozenset(catalog.attachment_entry("ur5").supported_robots)
Z1_SUPPORTED_ROBOTS = frozenset(catalog.attachment_entry("z1").supported_robots)
LIDAR_SUPPORTED_ROBOTS = frozenset(catalog.attachment_entry("lidar").supported_robots)
TERMINAL_CONTROLLER_STEP = 8
SCENE_CHOICES = catalog.SCENE_CHOICES
ROBOT_LABELS = catalog.ROBOT_LABELS


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
    selected: list[str] = []
    for char in text:
        if not char.isdigit():
            raise ValueError(f"Invalid robot number: {char}")
        index = 9 if char == "0" else int(char) - 1
        if index >= len(robot_keys):
            raise ValueError(f"Robot number out of range: {char}")
        selected.append(robot_keys[index])
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
    print_func("\n未指定 --env，进入 EAI 交互式环境选择。")
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
            print_func("\nPayloads / Manipulators")
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
            print_func("\nPayloads / Sensors")
            updated = _choose_attachments(
                "GSHub",
                "gshub",
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
                "LiDAR",
                "lidar",
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
            print_func("\nTools")
            updated = _choose_attachments(
                "ROS tool",
                "ros",
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
            updated = _choose_attachments(
                "Keyboard tool",
                "keyboard",
                robots,
                input_func=input_func,
                print_func=print_func,
            )
            if updated is None:
                step = 6
                continue
            robots = updated
            before_controller_override = list(robots)
            step = TERMINAL_CONTROLLER_STEP
            continue

        updated = _maybe_override_controllers(
            before_controller_override,
            input_func=input_func,
            print_func=print_func,
        )
        if updated is None:
            step = 7
            continue
        if scene_key is None:
            raise RuntimeError("Scene was not selected.")
        return InteractiveSelection(scene_key=scene_key, robots=tuple(updated))


def _choose_scene(*, allow_back: bool, input_func, print_func) -> str | None:
    print_func("\n请选择场景:")
    for index, (_key, label) in enumerate(SCENE_CHOICES, start=1):
        print_func(f"  {index}. {label}")
    while True:
        raw = input_func(f"输入编号 [1-{len(SCENE_CHOICES)}]，默认 1: ").strip()
        if allow_back and is_back_token(raw):
            return None
        if not raw:
            return SCENE_CHOICES[0][0]
        try:
            value = int(raw)
        except ValueError:
            print_func("请输入数字编号。")
            continue
        if 1 <= value <= len(SCENE_CHOICES):
            return SCENE_CHOICES[value - 1][0]
        print_func("编号超出范围。")


def _choose_robot_digits(*, input_func, print_func) -> tuple[str, ...] | None:
    print_func("\n请选择机器人，可直接输入数字串，重复数字代表多个同类机器人。")
    for index, key in enumerate(ROBOT_KEYS, start=1):
        label = "0" if index == 10 else str(index)
        print_func(f"  {label}. {ROBOT_LABELS.get(key, key)}")
    while True:
        raw = input_func("输入机器人编号串，例如 12357，默认 1: ").strip()
        if not raw:
            raw = "1"
        if is_back_token(raw):
            return None
        try:
            return parse_robot_digit_selection(raw)
        except ValueError as exc:
            print_func(f"输入无效: {exc}")


def _choose_attachments(
    label: str,
    attachment_type: str,
    robots: list[RobotSelection],
    *,
    input_func,
    print_func,
) -> list[RobotSelection] | None:
    candidates = [
        (index, robot)
        for index, robot in enumerate(robots, start=1)
        if attachment_supported(robot.type, attachment_type)
        and not (
            attachment_type in {"ur5", "z1"}
            and any(item.type in {"ur5", "z1"} for item in robot.attachments)
        )
    ]
    if not candidates:
        print_func(f"\n没有可装配 {label} 的机器人。")
        return robots
    print_func(f"\n可装配 {label} 的机器人:")
    for index, robot in candidates:
        print_func(f"  {index}. {robot.type}_{index}")
    while True:
        raw = input_func(f"输入需要装配 {label} 的机器人编号，空为不装配: ").strip()
        if not raw:
            return robots
        if is_back_token(raw):
            return None
        try:
            selected = _parse_attachment_indices(raw)
        except ValueError:
            print_func("请输入编号，多个编号用空格或逗号分隔。")
            continue
        valid = {index for index, _robot in candidates}
        if not selected <= valid:
            print_func("编号包含不可装配的机器人。")
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
        return updated


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

    print_func("\n可用 controller cfg:")
    for name in controller_cfg_names():
        print_func(f"  - {name}")
    updated = list(robots)
    while True:
        options: list[tuple[str, int, str | None]] = []
        print_func("\n可修改 controller 的对象:")
        display_index = 1
        for robot_index, robot in enumerate(updated):
            print_func(f"  {display_index}. {robot.type}_{robot_index + 1} base ({robot.controller.cfg})")
            options.append(("robot", robot_index, None))
            display_index += 1
            for attachment in robot.attachments:
                if attachment.controller is not None:
                    print_func(f"  {display_index}. {robot.type}_{robot_index + 1} {attachment.type} ({attachment.controller.cfg})")
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
            print_func("请输入数字编号。")
            continue
        if not (1 <= target <= len(options)):
            print_func("编号超出范围。")
            continue
        cfg_name = input_func("输入新的 cfg 名称: ").strip()
        if is_back_token(cfg_name):
            continue
        if cfg_name not in controller_cfg_names():
            print_func(f"未知 cfg: {cfg_name}")
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
        print_func("请输入 y、n 或 b。")


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
        scene_key=str(data["scene_key"]),
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
