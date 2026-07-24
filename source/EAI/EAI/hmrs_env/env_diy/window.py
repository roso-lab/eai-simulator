from __future__ import annotations

import argparse
import json
import sys
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from PIL import Image, ImageTk

from . import catalog as shared_catalog
from .catalog import AttachmentCatalogEntry, RobotCatalogEntry
from .paths import REPO_ROOT


PICTURE_ROOT = REPO_ROOT / "usd" / "picture"

try:
    from .storage import save_task_with_payload, task_from_visual_state
except ImportError:
    TASK_DIY_ROOT = Path(__file__).resolve().parent
    if str(TASK_DIY_ROOT) not in sys.path:
        sys.path.insert(0, str(TASK_DIY_ROOT))
    from storage import save_task_with_payload, task_from_visual_state
ROBOT_NAMES = (
    "M20",
    "b2",
    "carter",
    "cf2x",
    "g1",
    "go2",
    "human",
    "lite3",
    "mushr_v2",
    "pepper",
    "scout",
)
DEFAULT_SENSOR_ORDER = ("gshub", "lidar")
DEFAULT_MANIPULATOR_ORDER = ("ur5", "z1")
MANIPULATOR_NAMES = DEFAULT_MANIPULATOR_ORDER
SENSOR_NAMES = ("gshub", "lidar")
VISUAL_SENSOR_SUPPORTED_ROBOTS = tuple(robot for robot in ROBOT_NAMES if robot != "human")
GSHUB_SUPPORTED_ROBOTS = shared_catalog.attachment_entry("gshub").supported_robots
ROS_TOOL_SUPPORTED_ROBOTS = shared_catalog.tool_catalog()["ros"].supported_robots
TOOL_NAMES = ("ros", "keyboard")  # 与外部系统连接的工具（通信/控制通道）
PALETTE_MODES = ("scenes", "robots", "payloads", "manipulators", "sensors", "tools")
PALETTE_TOP_LEVEL_MODES = ("scenes", "robots", "payloads", "tools")
PAYLOAD_MODES = ("manipulators", "sensors")
MANIPULATOR_TYPES = frozenset(MANIPULATOR_NAMES)


def _round_coord(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


@dataclass(frozen=True)
class AssetOption:
    name: str
    path: Path


@dataclass(frozen=True)
class DragPayload:
    kind: str
    name: str
    cfg_mode: str = "default"


class PaletteMode:
    def __init__(self) -> None:
        self.active = "scenes"
        self.payload_active = "manipulators"

    def select(self, mode: str) -> None:
        if mode not in PALETTE_MODES:
            raise ValueError(f"Unknown palette mode: {mode}")
        if mode in PAYLOAD_MODES:
            self.active = "payloads"
            self.payload_active = mode
        else:
            self.active = mode

    def select_payload(self, mode: str) -> None:
        if mode not in PAYLOAD_MODES:
            raise ValueError(f"Unknown payload mode: {mode}")
        self.active = "payloads"
        self.payload_active = mode

    def after_scene_drop(self) -> None:
        self.active = "robots"

    def after_robot_drop(self) -> None:
        self.active = "robots"

    def reset(self) -> None:
        self.active = "scenes"
        self.payload_active = "manipulators"


@dataclass(frozen=True)
class ControllerSelection:
    mode: str = "default"
    cfg: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        if self.mode == "manual":
            return {"mode": "manual", "cfg": self.cfg}
        return {"mode": "default", "cfg": self.cfg}


@dataclass
class AttachmentItem:
    type: str
    x: float
    y: float
    asset_cfg: str | None
    robot_asset_variant: str | None
    controller: ControllerSelection | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "x": _round_coord(self.x),
            "y": _round_coord(self.y),
            "asset_cfg": self.asset_cfg,
            "robot_asset_variant": self.robot_asset_variant,
            "controller": self.controller.to_dict() if self.controller else None,
        }


def robot_catalog() -> dict[str, RobotCatalogEntry]:
    return shared_catalog.robot_catalog()


def attachment_catalog() -> dict[str, AttachmentCatalogEntry]:
    return shared_catalog.attachment_catalog()


def attachment_catalog_entry(attachment_name: str) -> AttachmentCatalogEntry:
    """查找 payload 或 tool 的 catalog 条目（统一接口）。"""
    return shared_catalog.attachment_entry(attachment_name)


def tool_catalog() -> dict[str, AttachmentCatalogEntry]:
    """Tool 类目录：与外部系统连接的工具（如 ROS2 通信通道）。

    与 payload 的区别：
    - payload = 挂载在宿主机器人上的机械臂或传感器
    - tool = 通信/控制通道（ROS2 发布、MQTT、远程控制等）

    例如 'ros' tool = 开启该机器人完整 ROS2 接口；'keyboard' tool = 仅开启 cmd_vel 控制订阅。
    """
    return shared_catalog.tool_catalog()


def discover_scenes(picture_root: Path = PICTURE_ROOT) -> list[AssetOption]:
    scene_dir = picture_root / "scene"
    return [AssetOption(path.stem, path) for path in sorted(scene_dir.glob("*.png"))]


def robot_image_path(robot: str, picture_root: Path = PICTURE_ROOT) -> Path:
    """Resolve a robot image, preferring the processed DIY thumbnail."""
    processed = picture_root / "processed" / "robot" / f"{robot}.png"
    if processed.exists():
        return processed
    return picture_root / "robot" / f"{robot}.png"


def discover_manipulator_names(picture_root: Path = PICTURE_ROOT) -> tuple[str, ...]:
    """Discover host-mounted manipulators, with a legacy sensor fallback."""
    manipulator_dir = picture_root / "processed" / "manipulator"
    discovered = {path.stem for path in manipulator_dir.glob("*.png")} if manipulator_dir.exists() else set()
    legacy_sensor_dir = picture_root / "processed" / "sensor"
    for name in DEFAULT_MANIPULATOR_ORDER:
        if (legacy_sensor_dir / f"{name}.png").exists():
            discovered.add(name)
    ordered = [name for name in DEFAULT_MANIPULATOR_ORDER if name in discovered]
    ordered.extend(sorted(discovered - set(DEFAULT_MANIPULATOR_ORDER)))
    return tuple(ordered)


def discover_sensor_names(picture_root: Path = PICTURE_ROOT) -> tuple[str, ...]:
    sensor_dir = picture_root / "processed" / "sensor"
    if not sensor_dir.exists():
        return ()
    discovered = {
        path.stem
        for path in sensor_dir.glob("*.png")
        if path.stem not in MANIPULATOR_TYPES
    }
    ordered = [name for name in DEFAULT_SENSOR_ORDER if name in discovered]
    ordered.extend(sorted(discovered - set(DEFAULT_SENSOR_ORDER)))
    return tuple(ordered)


def attachment_image_path(name: str, category: str) -> Path:
    """Resolve a payload icon, accepting legacy UR5/Z1 sensor locations."""
    group = "manipulator" if category == "manipulator" else category
    candidate = PICTURE_ROOT / "processed" / group / f"{name}.png"
    if candidate.exists():
        return candidate
    if category == "manipulator":
        return PICTURE_ROOT / "processed" / "sensor" / f"{name}.png"
    return candidate


def ask_yes_no(root: tk.Tk, message: str) -> bool:
    dialog = tk.Toplevel(root)
    dialog.title("EAI Env DIY")
    dialog.transient(root)
    dialog.grab_set()
    dialog.resizable(False, False)

    result = {"value": False}

    def submit() -> None:
        result["value"] = True
        dialog.destroy()

    def cancel() -> None:
        result["value"] = False
        dialog.destroy()

    ttk.Label(
        dialog,
        text=message,
        padding=(16, 14, 16, 8),
    ).grid(row=0, column=0, columnspan=2, sticky="ew")
    ttk.Button(dialog, text="是", command=submit).grid(row=1, column=0, padx=(16, 6), pady=(4, 16), sticky="e")
    ttk.Button(dialog, text="否", command=cancel).grid(row=1, column=1, padx=(6, 16), pady=(4, 16), sticky="w")

    dialog.protocol("WM_DELETE_WINDOW", cancel)
    dialog.bind("<Return>", lambda _event: submit())
    dialog.bind("<Escape>", lambda _event: cancel())
    dialog.update_idletasks()
    x = root.winfo_rootx() + max(0, (root.winfo_width() - dialog.winfo_width()) // 2)
    y = root.winfo_rooty() + max(0, (root.winfo_height() - dialog.winfo_height()) // 2)
    dialog.geometry(f"+{x}+{y}")
    dialog.focus_set()
    root.wait_window(dialog)
    return result["value"]


def ask_save_env(root: tk.Tk) -> bool:
    return ask_yes_no(root, "是否保存？")


def ask_execute_simulation(root: tk.Tk) -> bool:
    return ask_yes_no(root, "是否运行？")


@dataclass
class RobotItem:
    id: str
    type: str
    x: float
    y: float
    controller: ControllerSelection
    scale: float = 1
    attachments: list[AttachmentItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "x": _round_coord(self.x),
            "y": _round_coord(self.y),
            "scale": self.scale,
            "controller": self.controller.to_dict(),
            "attachments": [
                item.to_dict()
                for item in self.attachments
            ],
        }


class TaskState:
    def __init__(self) -> None:
        self.scene: str | None = None
        self.robots: list[RobotItem] = []
        self._next_robot_number = 1

    def set_scene(self, scene: str) -> None:
        self.scene = scene
        self.robots = []
        self._next_robot_number = 1

    def add_robot(self, robot_type: str, x: float, y: float, *, controller_mode: str = "default") -> str:
        robot_id = f"robot-{self._next_robot_number}"
        self._next_robot_number += 1
        robot_cfg = robot_catalog().get(robot_type)
        controller = ControllerSelection(
            mode="manual" if controller_mode == "manual" else "default",
            cfg=None if robot_cfg is None else robot_cfg.default_controller_cfg,
        )
        self.robots.append(RobotItem(robot_id, robot_type, _round_coord(x), _round_coord(y), controller))
        return robot_id

    def move_robot(self, robot_id: str, x: float, y: float) -> None:
        robot = self.robot_by_id(robot_id)
        if robot:
            robot.x = _round_coord(x)
            robot.y = _round_coord(y)

    def delete_robot(self, robot_id: str | None) -> None:
        if robot_id:
            self.robots = [robot for robot in self.robots if robot.id != robot_id]

    def attach_payload(self, robot_id: str, payload_type: str, x: float, y: float, *, controller_mode: str = "default") -> bool:
        robot = self.robot_by_id(robot_id)
        attachment_cfg = attachment_catalog_entry(payload_type)
        if not robot or not attachment_cfg.supports(robot.type):
            return False
        if payload_type in MANIPULATOR_TYPES and any(
            attachment.type in {"ur5", "z1"} for attachment in robot.attachments
        ):
            return False
        controller = None
        if attachment_cfg.controller_cfg:
            controller = ControllerSelection(
                mode="manual" if controller_mode == "manual" else "default",
                cfg=attachment_cfg.controller_cfg,
            )
        robot.attachments.append(
            AttachmentItem(
                payload_type,
                _round_coord(x),
                _round_coord(y),
                attachment_cfg.asset_cfg_for(robot.type),
                attachment_cfg.robot_asset_variant_for(robot.type),
                controller,
            )
        )
        return True

    def attach_sensor(self, robot_id: str, sensor_type: str, x: float, y: float, *, controller_mode: str = "default") -> bool:
        """Backward-compatible alias for callers using the old method name."""
        return self.attach_payload(robot_id, sensor_type, x, y, controller_mode=controller_mode)

    def robot_by_id(self, robot_id: str) -> RobotItem | None:
        return next((robot for robot in self.robots if robot.id == robot_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {"scene": self.scene, "robots": [robot.to_dict() for robot in self.robots]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def completion_issue(self) -> str | None:
        if not self.scene:
            return "scene"
        if not self.robots:
            return "robot"
        return None


class TaskDiyWindow:
    def __init__(self, root: tk.Tk, *, keyboard_preflight_output: Path | None = None) -> None:
        self.root = root
        self.root.title("EAI Env DIY")
        self.root.geometry("1180x760")
        self.root.minsize(920, 620)

        self.state = TaskState()
        self.palette_mode = PaletteMode()
        self.selected_robot_id: str | None = None
        self.drag_payload: DragPayload | None = None
        self.palette_drag_window: tk.Toplevel | None = None
        self.palette_drag_photo: ImageTk.PhotoImage | None = None
        self.drag_robot_id: str | None = None
        self.drag_offset = (0, 0)
        self.image_cache: dict[tuple[str, str, int, int], ImageTk.PhotoImage] = {}
        self.scene_original: Image.Image | None = None
        self.scene_photo: ImageTk.PhotoImage | None = None
        self.robot_canvas_items: dict[int, str] = {}
        self.palette_mode_buttons: dict[str, ttk.Button] = {}
        self.robot_controller_vars: dict[str, tk.StringVar] = {}
        self.attachment_controller_vars: dict[str, tk.StringVar] = {}
        self.keyboard_preflight_output = keyboard_preflight_output

        self._build_ui()
        self._render_palette()
        self._render_canvas()
        self._update_json()

    def _build_ui(self) -> None:
        self._configure_styles()
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=0)
        self.root.rowconfigure(0, weight=1)

        left = ttk.Frame(self.root, padding=12, style="App.TFrame")
        left.grid(row=0, column=0, sticky="nsew")
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(left, style="App.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="EAI Env DIY", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.status_var = tk.StringVar(value="Scene palette active.")
        ttk.Label(toolbar, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=1, sticky="w", padx=14)
        ttk.Button(toolbar, text="Complete Selection", style="Accent.TButton", command=self._complete_selection).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(toolbar, text="Delete Robot", style="Tool.TButton", command=self._delete_selected_robot).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(toolbar, text="Reset", style="Tool.TButton", command=self._reset).grid(row=0, column=4)

        self.canvas = tk.Canvas(left, bg="#0f1720", highlightthickness=1, highlightbackground="#7fbfff")
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self._render_canvas())
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.root.bind("<Delete>", lambda _event: self._delete_selected_robot())
        self.root.bind("<BackSpace>", lambda _event: self._delete_selected_robot())

        right = ttk.Frame(self.root, padding=12, style="Side.TFrame")
        right.grid(row=0, column=1, sticky="ns")
        self.right_panel = right
        right.rowconfigure(3, weight=1)

        self.palette_title = ttk.Label(right, text="Scenes", style="PanelTitle.TLabel")
        self.palette_title.grid(row=0, column=0, sticky="w")

        mode_bar = ttk.Frame(right, style="Side.TFrame")
        mode_bar.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        for index, mode in enumerate(PALETTE_TOP_LEVEL_MODES):
            mode_bar.columnconfigure(index, weight=1)
            button = ttk.Button(
                mode_bar,
                text=mode.title(),
                style="Tab.TButton",
                command=lambda selected=mode: self._select_palette_mode(selected),
            )
            button.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 4, 0))
            self.palette_mode_buttons[mode] = button

        self.payload_mode_bar = ttk.Frame(right, style="Side.TFrame")
        self.payload_mode_bar.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.payload_mode_buttons: dict[str, ttk.Button] = {}
        for index, mode in enumerate(PAYLOAD_MODES):
            self.payload_mode_bar.columnconfigure(index, weight=1)
            button = ttk.Button(
                self.payload_mode_bar,
                text=mode.title(),
                style="Tab.TButton",
                command=lambda selected=mode: self._select_palette_mode(selected),
            )
            button.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 4, 0))
            self.payload_mode_buttons[mode] = button

        palette_shell = ttk.Frame(right, style="Side.TFrame")
        palette_shell.grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        palette_shell.rowconfigure(0, weight=1)
        palette_shell.columnconfigure(0, weight=1)
        self.palette_canvas = tk.Canvas(palette_shell, width=340, highlightthickness=0, bg="#f4f8fc")
        self.palette_canvas.grid(row=0, column=0, sticky="nsew")
        palette_scrollbar = ttk.Scrollbar(palette_shell, orient="vertical", command=self.palette_canvas.yview)
        palette_scrollbar.grid(row=0, column=1, sticky="ns")
        self.palette_canvas.configure(yscrollcommand=palette_scrollbar.set)
        self.palette = ttk.Frame(self.palette_canvas, style="Side.TFrame")
        self.palette_window = self.palette_canvas.create_window((0, 0), window=self.palette, anchor="nw")
        self.palette.bind(
            "<Configure>",
            lambda event: self.palette_canvas.configure(scrollregion=self.palette_canvas.bbox("all")),
        )
        self.palette_canvas.bind(
            "<Configure>",
            lambda event: self.palette_canvas.itemconfigure(self.palette_window, width=event.width),
        )

        json_tools = ttk.Frame(right, style="Side.TFrame")
        json_tools.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        json_tools.columnconfigure(2, weight=1)
        ttk.Button(json_tools, text="Copy JSON", style="Tool.TButton", command=self._copy_json).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(json_tools, text="保存", style="Tool.TButton", command=self._save_task).grid(row=0, column=1, sticky="w")

        self.json_text = tk.Text(right, width=42, height=16, wrap="none", bg="#0f1720", fg="#d8e7ef")
        self.json_text.grid(row=5, column=0, sticky="ew")

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background="#edf3f8")
        style.configure("Side.TFrame", background="#f4f8fc")
        style.configure("Card.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("Title.TLabel", background="#edf3f8", foreground="#102030", font=("", 15, "bold"))
        style.configure("Status.TLabel", background="#edf3f8", foreground="#526170")
        style.configure("PanelTitle.TLabel", background="#f4f8fc", foreground="#102030", font=("", 13, "bold"))
        style.configure("CardTitle.TLabel", background="#ffffff", foreground="#102030", font=("", 10, "bold"))
        style.configure("CardMeta.TLabel", background="#ffffff", foreground="#5c6b7a", font=("", 8))
        style.configure(
            "Tool.TButton",
            padding=(10, 6),
            background="#ffffff",
            foreground="#102030",
            bordercolor="#c8d6e3",
            lightcolor="#ffffff",
            darkcolor="#c8d6e3",
            relief="flat",
        )
        style.map("Tool.TButton", background=[("active", "#e8f4ff")], bordercolor=[("active", "#7fc8ff")])
        style.configure(
            "Accent.TButton",
            padding=(12, 7),
            background="#d7f0ff",
            foreground="#004769",
            bordercolor="#49bfff",
            lightcolor="#ecf9ff",
            darkcolor="#49bfff",
            relief="flat",
        )
        style.map("Accent.TButton", background=[("active", "#bde7ff")], bordercolor=[("active", "#00a7ef")])
        style.configure(
            "Tab.TButton",
            padding=(8, 7),
            background="#ffffff",
            foreground="#3d4b59",
            bordercolor="#d5e0ea",
            lightcolor="#ffffff",
            darkcolor="#d5e0ea",
            relief="flat",
        )
        style.configure(
            "Active.Tab.TButton",
            padding=(8, 7),
            background="#d7f0ff",
            foreground="#005e86",
            bordercolor="#55c3ff",
            lightcolor="#ecf9ff",
            darkcolor="#55c3ff",
            relief="flat",
        )
        style.map("Tab.TButton", background=[("active", "#edf8ff")], bordercolor=[("active", "#9ad7ff")])

    def _render_palette(self) -> None:
        if self.palette_mode.active == "scenes":
            self._render_palette_scene_mode()
        elif self.palette_mode.active == "robots":
            self._render_palette_robot_mode()
        elif self.palette_mode.active == "payloads":
            if self.palette_mode.payload_active == "manipulators":
                self._render_palette_manipulator_mode()
            else:
                self._render_palette_sensor_mode()
        elif self.palette_mode.active == "tools":
            self._render_palette_tool_mode()
        self._update_palette_mode_buttons()

    def _select_palette_mode(self, mode: str) -> None:
        self.palette_mode.select(mode)
        self._render_palette()
        self.status_var.set(f"{mode.title()} palette active.")

    def _update_palette_mode_buttons(self) -> None:
        for mode, button in self.palette_mode_buttons.items():
            button.configure(style="Active.Tab.TButton" if mode == self.palette_mode.active else "Tab.TButton")
        payload_visible = self.palette_mode.active == "payloads"
        if payload_visible:
            self.payload_mode_bar.grid()
        else:
            self.payload_mode_bar.grid_remove()
        for mode, button in self.payload_mode_buttons.items():
            button.configure(
                style="Active.Tab.TButton" if payload_visible and mode == self.palette_mode.payload_active else "Tab.TButton"
            )

    def _render_palette_scene_mode(self) -> None:
        self._clear_palette()
        self.palette_title.config(text="Scenes")
        for index, scene in enumerate(discover_scenes()):
            self._asset_card("scene", scene.name, scene.path, 300, 150).grid(
                row=index, column=0, sticky="ew", pady=8
            )

    def _render_palette_robot_mode(self) -> None:
        self._clear_palette()
        self.palette_title.config(text="Robots")
        row = 0
        catalog = robot_catalog()
        for robot in ROBOT_NAMES:
            self._robot_card(robot, catalog[robot]).grid(row=row, column=0, sticky="ew", pady=5)
            row += 1

    def _render_palette_manipulator_mode(self) -> None:
        self._clear_palette()
        self.palette_title.config(text="Payloads / Manipulators")
        row = 0
        for manipulator in discover_manipulator_names():
            entry = attachment_catalog_entry(manipulator)
            self._attachment_card(manipulator, entry).grid(row=row, column=0, sticky="ew", pady=5)
            row += 1

    def _render_palette_sensor_mode(self) -> None:
        self._clear_palette()
        self.palette_title.config(text="Payloads / Sensors")
        row = 0
        for sensor in discover_sensor_names():
            self._attachment_card(sensor, attachment_catalog_entry(sensor)).grid(row=row, column=0, sticky="ew", pady=5)
            row += 1

    def _render_palette_tool_mode(self) -> None:
        self._clear_palette()
        self.palette_title.config(text="Tools")
        row = 0
        catalog = tool_catalog()
        for tool_name in TOOL_NAMES:
            self._tool_card(tool_name, catalog[tool_name]).grid(row=row, column=0, sticky="ew", pady=5)
            row += 1

    def _asset_card(self, kind: str, label: str, image_path: Path, width: int, height: int) -> ttk.Frame:
        frame = ttk.Frame(self.palette, padding=8, style="Card.TFrame")
        frame.columnconfigure(0, weight=1)
        photo = self._thumbnail(image_path, width, height)
        image_label = ttk.Label(frame, image=photo, anchor="center")
        image_label.image = photo
        image_label.grid(row=0, column=0, sticky="ew")
        title_label = ttk.Label(frame, text=label, anchor="center", style="CardTitle.TLabel")
        title_label.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        for widget in (frame, image_label, title_label):
            widget.bind("<ButtonPress-1>", lambda event, k=kind, name=label, path=image_path: self._begin_palette_drag(event, k, name, path))
            widget.bind("<B1-Motion>", self._move_palette_drag)
            widget.bind("<ButtonRelease-1>", self._release_palette_drag)
        return frame

    def _robot_card(self, robot: str, entry: RobotCatalogEntry) -> ttk.Frame:
        frame = ttk.Frame(self.palette, padding=8, style="Card.TFrame")
        frame.columnconfigure(1, weight=1)
        image_path = robot_image_path(robot)
        photo = self._thumbnail(image_path, 92, 70)
        image_label = ttk.Label(frame, image=photo, anchor="center", background="#ffffff")
        image_label.image = photo
        image_label.grid(row=0, column=0, rowspan=3, padx=(0, 10), sticky="w")
        title_label = ttk.Label(frame, text=robot, style="CardTitle.TLabel")
        title_label.grid(row=0, column=1, sticky="w")
        default_label = f"Default ({entry.default_controller_cfg})" if entry.default_controller_cfg else "Default (Not configured)"
        cfg_var = tk.StringVar(value=default_label)
        self.robot_controller_vars[robot] = cfg_var
        selector = ttk.Combobox(frame, textvariable=cfg_var, values=(default_label, "Manual"), state="readonly", width=24)
        selector.grid(row=1, column=1, sticky="ew", pady=(4, 2))
        hint_label = ttk.Label(frame, text="Drag to add another robot", style="CardMeta.TLabel")
        hint_label.grid(row=2, column=1, sticky="w")
        for widget in (frame, image_label, title_label, hint_label):
            widget.bind("<ButtonPress-1>", lambda event, name=robot, path=image_path: self._begin_palette_drag(event, "robot", name, path))
            widget.bind("<B1-Motion>", self._move_palette_drag)
            widget.bind("<ButtonRelease-1>", self._release_palette_drag)
        return frame

    def _attachment_card(self, payload: str, entry: AttachmentCatalogEntry) -> ttk.Frame:
        frame = ttk.Frame(self.palette, padding=8, style="Card.TFrame")
        frame.columnconfigure(1, weight=1)
        image_path = attachment_image_path(payload, entry.category)
        photo = self._thumbnail(image_path, 92, 70)
        image_label = ttk.Label(frame, image=photo, anchor="center", background="#ffffff")
        image_label.image = photo
        image_label.grid(row=0, column=0, rowspan=3, padx=(0, 10), sticky="w")
        title_label = ttk.Label(frame, text=payload, style="CardTitle.TLabel")
        title_label.grid(row=0, column=1, sticky="w")
        drag_widgets = [frame, image_label, title_label]
        if entry.controller_cfg:
            default_label = f"Default ({entry.controller_cfg})"
            values = (default_label, "Manual")
            cfg_var = tk.StringVar(value=default_label)
            selector = ttk.Combobox(frame, textvariable=cfg_var, values=values, state="readonly", width=24)
            selector.grid(row=1, column=1, sticky="ew", pady=(4, 2))
            self.attachment_controller_vars[payload] = cfg_var
        else:
            asset_text = "Visual-only asset" if entry.visual_only else f"Asset: {entry.asset_cfg}"
            asset_label = ttk.Label(frame, text=asset_text, style="CardMeta.TLabel")
            asset_label.grid(row=1, column=1, sticky="w", pady=(4, 2))
            drag_widgets.append(asset_label)
        supported = ", ".join(entry.supported_robots)
        supported_label = ttk.Label(frame, text=f"Supports: {supported}", style="CardMeta.TLabel", wraplength=210)
        supported_label.grid(row=2, column=1, sticky="w")
        drag_widgets.append(supported_label)
        for widget in drag_widgets:
            widget.bind("<ButtonPress-1>", lambda event, name=payload, path=image_path: self._begin_palette_drag(event, "payload", name, path))
            widget.bind("<B1-Motion>", self._move_palette_drag)
            widget.bind("<ButtonRelease-1>", self._release_palette_drag)
        return frame

    def _tool_card(self, tool: str, entry: AttachmentCatalogEntry) -> ttk.Frame:
        """工具卡片（类似 attachment_card，但语义是'通信通道'而非'物理硬件'）。"""
        frame = ttk.Frame(self.palette, padding=8, style="Card.TFrame")
        frame.columnconfigure(1, weight=1)
        image_path = PICTURE_ROOT / "processed" / "tool" / f"{tool}.png"
        photo = self._thumbnail(image_path, 92, 70)
        image_label = ttk.Label(frame, image=photo, anchor="center", background="#ffffff")
        image_label.image = photo
        image_label.grid(row=0, column=0, rowspan=3, padx=(0, 10), sticky="w")
        title_label = ttk.Label(frame, text=tool.upper(), style="CardTitle.TLabel")
        title_label.grid(row=0, column=1, sticky="w")

        hint = "Enable keyboard cmd_vel control only" if tool == "keyboard" else f"Enable {tool.upper()} for this robot + payloads"
        hint_label = ttk.Label(frame, text=hint, style="CardMeta.TLabel", wraplength=210)
        hint_label.grid(row=1, column=1, sticky="w", pady=(4, 2))

        supported = ", ".join(entry.supported_robots) if entry.supported_robots else "All robots"
        supported_label = ttk.Label(frame, text=f"Supports: {supported}", style="CardMeta.TLabel", wraplength=210)
        supported_label.grid(row=2, column=1, sticky="w")

        for widget in (frame, image_label, title_label, hint_label, supported_label):
            widget.bind("<ButtonPress-1>", lambda event, name=tool, path=image_path: self._begin_palette_drag(event, "tool", name, path))
            widget.bind("<B1-Motion>", self._move_palette_drag)
            widget.bind("<ButtonRelease-1>", self._release_palette_drag)
        return frame

    def _clear_palette(self) -> None:
        for child in self.palette.winfo_children():
            child.destroy()

    def _set_scene(self, scene: str) -> None:
        self.state.set_scene(scene)
        self.selected_robot_id = None
        self.drag_payload = None
        self.scene_original = Image.open(PICTURE_ROOT / "scene" / f"{scene}.png").convert("RGB")
        self.palette_mode.after_scene_drop()
        self._render_palette()
        self._render_canvas()
        self._update_json()
        self.status_var.set(f"{scene} scene loaded.")

    def _render_canvas(self) -> None:
        self.canvas.delete("all")
        self.robot_canvas_items.clear()
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())

        if self.scene_original is None:
            self.canvas.create_text(
                width // 2,
                height // 2,
                text="No scene selected",
                fill="#d8e7ef",
                font=("", 18, "bold"),
            )
            return

        scene = self.scene_original.copy()
        scene.thumbnail((width, height), Image.Resampling.LANCZOS)
        self.scene_photo = ImageTk.PhotoImage(scene)
        self.canvas.create_image(width // 2, height // 2, image=self.scene_photo, anchor="center")

        for robot in self.state.robots:
            self._draw_robot(robot, width, height)

    def _draw_robot(self, robot: RobotItem, width: int, height: int) -> None:
        size = int(112 * robot.scale)
        photo = self._thumbnail(robot_image_path(robot.type), size, size)
        x = int(robot.x * width)
        y = int(robot.y * height)
        item = self.canvas.create_image(x, y, image=photo, anchor="center")
        self.robot_canvas_items[item] = robot.id
        self.canvas.tag_bind(item, "<Button-1>", lambda event, rid=robot.id: self._select_canvas_robot(event, rid))
        self.canvas.tag_bind(item, "<B1-Motion>", self._on_canvas_drag)
        self.canvas.tag_bind(item, "<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.create_text(x, y + size // 2 + 12, text=robot.type, fill="#eef6ff", font=("", 9, "bold"))
        if robot.id == self.selected_robot_id:
            self.canvas.create_rectangle(x - size // 2, y - size // 2, x + size // 2, y + size // 2, outline="#00b7ff", width=2)

        for attachment in robot.attachments:
            entry = attachment_catalog_entry(attachment.type)
            icon_path = attachment_image_path(attachment.type, entry.category)
            sensor_photo = self._thumbnail(icon_path, 34, 34)
            sx = int(x - size // 2 + attachment.x * size)
            sy = int(y - size // 2 + attachment.y * size)
            self.canvas.create_image(sx, sy, image=sensor_photo, anchor="center")

    def _on_canvas_click(self, event: tk.Event) -> None:
        robot_id = self._robot_at(event.x, event.y)
        self.selected_robot_id = robot_id
        self.drag_robot_id = robot_id
        if robot_id:
            robot = self.state.robot_by_id(robot_id)
            self.drag_offset = (
                event.x - int(robot.x * self.canvas.winfo_width()),
                event.y - int(robot.y * self.canvas.winfo_height()),
            )
        self._render_canvas()

    def _select_canvas_robot(self, event: tk.Event, robot_id: str) -> str:
        self.selected_robot_id = robot_id
        self.drag_robot_id = robot_id
        robot = self.state.robot_by_id(robot_id)
        if robot:
            self.drag_offset = (
                event.x - int(robot.x * self.canvas.winfo_width()),
                event.y - int(robot.y * self.canvas.winfo_height()),
            )
        self._render_canvas()
        return "break"

    def _on_canvas_drag(self, event: tk.Event) -> None:
        if not self.drag_robot_id:
            return
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        self.state.move_robot(
            self.drag_robot_id,
            (event.x - self.drag_offset[0]) / width,
            (event.y - self.drag_offset[1]) / height,
        )
        self._render_canvas()
        self._update_json()

    def _on_canvas_release(self, _event: tk.Event) -> None:
        if self.drag_robot_id and self._pointer_over_right_panel(_event.x_root, _event.y_root):
            deleted_id = self.drag_robot_id
            self.state.delete_robot(deleted_id)
            if self.selected_robot_id == deleted_id:
                self.selected_robot_id = None
            self.status_var.set("Robot removed.")
            self._render_canvas()
            self._update_json()
        self.drag_robot_id = None

    def _robot_at(self, x: int, y: int) -> str | None:
        candidates = self.canvas.find_overlapping(x, y, x, y)
        for item in reversed(candidates):
            robot_id = self.robot_canvas_items.get(item)
            if robot_id:
                return robot_id
        return None

    def _attach_payload_at(self, robot_id: str, x: int, y: int) -> None:
        robot = self.state.robot_by_id(robot_id)
        if not robot or not self.drag_payload or self.drag_payload.kind not in ("payload", "sensor", "tool"):
            return
        size = int(112 * robot.scale)
        center_x = robot.x * self.canvas.winfo_width()
        center_y = robot.y * self.canvas.winfo_height()
        local_x = (x - (center_x - size / 2)) / size
        local_y = (y - (center_y - size / 2)) / size
        payload_name = self.drag_payload.name if self.drag_payload else ""
        attached = self.state.attach_payload(
            robot_id,
            payload_name,
            local_x,
            local_y,
            controller_mode=self._controller_mode_for_attachment(payload_name),
        )
        if not attached:
            self.status_var.set(f"{payload_name} is not supported on {robot.type}.")
            return
        self.selected_robot_id = robot_id
        self._render_canvas()
        self._update_json()
        if self.drag_payload:
            self.status_var.set(f"{self.drag_payload.name} attached.")

    def _attach_sensor_at(self, robot_id: str, x: int, y: int) -> None:
        """Backward-compatible alias for the pre-payload UI helper."""
        self._attach_payload_at(robot_id, x, y)

    def _delete_selected_robot(self) -> None:
        if not self.selected_robot_id:
            return
        self.state.delete_robot(self.selected_robot_id)
        self.selected_robot_id = None
        self._render_canvas()
        self._update_json()
        self.status_var.set("Robot deleted.")

    def _reset(self) -> None:
        self.state = TaskState()
        self.palette_mode.reset()
        self.selected_robot_id = None
        self.drag_payload = None
        self.scene_original = None
        self.scene_photo = None
        self._render_palette()
        self._render_canvas()
        self._update_json()
        self.status_var.set("Scene palette active.")

    def _update_json(self) -> None:
        self.json_text.configure(state="normal")
        self.json_text.delete("1.0", "end")
        self.json_text.insert("1.0", self.state.to_json())
        self.json_text.configure(state="disabled")

    def _copy_json(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.state.to_json())
        self.status_var.set("JSON copied.")

    def _save_task(self) -> None:
        issue = self.state.completion_issue()
        if issue:
            self._complete_selection()
            return
        save_env = ask_save_env(self.root)
        env_name = "unsaved_env"
        saved_task = None
        selection_data = task_from_visual_state(env_name, self.state.to_dict())
        if save_env:
            task_name = self._ask_task_name()
            if task_name:
                try:
                    task_data = task_from_visual_state(task_name, self.state.to_dict())
                    path, saved_task = save_task_with_payload(
                        task_name,
                        task_data,
                    )
                except ValueError as exc:
                    messagebox.showerror("EAI Env DIY", str(exc))
                    return
                env_name = task_name
                selection_data = saved_task
                self.status_var.set(f"Env saved: {path.name}")
            else:
                self.status_var.set("Env not saved.")
        should_run = ask_execute_simulation(self.root)
        if self.keyboard_preflight_output is not None:
            self.keyboard_preflight_output.parent.mkdir(parents=True, exist_ok=True)
            self.keyboard_preflight_output.write_text(
                json.dumps(
                    {
                        "should_run": should_run,
                        "selection": selection_data,
                        "saved_task": saved_task,
                    }
                ),
                encoding="utf-8",
            )
            self.root.destroy()
        elif should_run and saved_task is not None:
            messagebox.showinfo(
                "EAI Env DIY",
                f"Run: python simulator.py --env={env_name} --num_envs=1 --device=cuda:0",
            )
        elif should_run:
            messagebox.showinfo(
                "EAI Env DIY",
                "Current Env is not saved. Launch from python simulator.py and run directly, or save it before using --env.",
            )

    def _ask_task_name(self) -> str | None:
        dialog = tk.Toplevel(self.root)
        dialog.title("保存 Env")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        ttk.Label(dialog, text="Env 名称:").grid(row=0, column=0, padx=12, pady=(12, 6), sticky="w")
        value = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=value, width=32)
        entry.grid(row=1, column=0, columnspan=2, padx=12, sticky="ew")
        result: dict[str, str | None] = {"value": None}

        def submit() -> None:
            result["value"] = value.get().strip()
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        ttk.Button(dialog, text="保存", command=submit).grid(row=2, column=0, padx=12, pady=12, sticky="e")
        ttk.Button(dialog, text="取消", command=cancel).grid(row=2, column=1, padx=12, pady=12, sticky="w")
        entry.bind("<Return>", lambda _event: submit())
        entry.focus_set()
        self.root.wait_window(dialog)
        return result["value"]

    def _complete_selection(self) -> None:
        issue = self.state.completion_issue()
        if issue == "scene":
            self.status_var.set("Select a scene before completing.")
            messagebox.showwarning("EAI Env DIY", "Please select a scene first.")
            return
        if issue == "robot":
            self.status_var.set("Add at least one robot before completing.")
            messagebox.showwarning("EAI Env DIY", "Please add at least one robot.")
            return
        self._update_json()
        self.status_var.set("Selection complete. JSON is ready.")

    def _thumbnail(self, path: Path, max_width: int, max_height: int) -> ImageTk.PhotoImage:
        key = (str(path), max_width, max_height)
        if key in self.image_cache:
            return self.image_cache[key]
        with Image.open(path) as opened:
            image = opened.convert("RGBA")
        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image)
        self.image_cache[key] = photo
        return photo

    def _begin_palette_drag(self, event: tk.Event, kind: str, name: str, image_path: Path) -> None:
        cfg_mode = "default"
        if kind == "robot":
            cfg_mode = self._controller_mode_for_robot(name)
        elif kind in {"sensor", "payload"}:
            cfg_mode = self._controller_mode_for_attachment(name)
        self.drag_payload = DragPayload(kind, name, cfg_mode)
        self.status_var.set(f"Dragging {name}.")
        self.palette_drag_photo = self._thumbnail(image_path, 96 if kind != "scene" else 180, 80 if kind != "scene" else 100)
        self.palette_drag_window = tk.Toplevel(self.root)
        self.palette_drag_window.overrideredirect(True)
        self.palette_drag_window.attributes("-topmost", True)
        label = ttk.Label(self.palette_drag_window, image=self.palette_drag_photo, relief="solid")
        label.pack()
        self._move_drag_window(event.x_root, event.y_root)

    def _move_palette_drag(self, event: tk.Event) -> None:
        self._move_drag_window(event.x_root, event.y_root)

    def _release_palette_drag(self, event: tk.Event) -> None:
        payload = self.drag_payload
        self._destroy_drag_window()
        self.drag_payload = None
        if not payload:
            return
        canvas_x = self.canvas.winfo_rootx()
        canvas_y = self.canvas.winfo_rooty()
        x = event.x_root - canvas_x
        y = event.y_root - canvas_y
        if not (0 <= x <= self.canvas.winfo_width() and 0 <= y <= self.canvas.winfo_height()):
            self.status_var.set("Drop cancelled.")
            return
        self._drop_payload(payload, x, y)

    def _drop_payload(self, payload: DragPayload, x: int, y: int) -> None:
        if payload.kind == "scene":
            self._set_scene(payload.name)
            return
        if self.scene_original is None:
            self.status_var.set("Select a scene first.")
            return
        if payload.kind == "robot":
            self.selected_robot_id = self.state.add_robot(
                payload.name,
                x / max(1, self.canvas.winfo_width()),
                y / max(1, self.canvas.winfo_height()),
                controller_mode=payload.cfg_mode,
            )
            self._render_canvas()
            self._update_json()
            self.status_var.set(f"{payload.name} placed.")
            self.palette_mode.after_robot_drop()
            self._render_palette()
            return
        if payload.kind == "sensor":
            payload = DragPayload("payload", payload.name, payload.cfg_mode)
        if payload.kind == "payload":
            robot_id = self._robot_at(x, y)
            if not robot_id:
                self.status_var.set("Robot target required.")
                return
            self.drag_payload = payload
            self._attach_payload_at(robot_id, x, y)
            self.drag_payload = None
        if payload.kind == "tool":
            robot_id = self._robot_at(x, y)
            if not robot_id:
                self.status_var.set("Robot target required.")
                return
            self.drag_payload = payload
            self._attach_payload_at(robot_id, x, y)
            self.drag_payload = None

    def _controller_mode_for_robot(self, robot: str) -> str:
        var = self.robot_controller_vars.get(robot)
        return "manual" if var and var.get() == "Manual" else "default"

    def _controller_mode_for_attachment(self, sensor: str) -> str:
        var = self.attachment_controller_vars.get(sensor)
        return "manual" if var and var.get() == "Manual" else "default"

    def _move_drag_window(self, x_root: int, y_root: int) -> None:
        if self.palette_drag_window:
            self.palette_drag_window.geometry(f"+{x_root + 12}+{y_root + 12}")

    def _destroy_drag_window(self) -> None:
        if self.palette_drag_window:
            self.palette_drag_window.destroy()
        self.palette_drag_window = None
        self.palette_drag_photo = None

    def _pointer_over_right_panel(self, x_root: int, y_root: int) -> bool:
        left = self.right_panel.winfo_rootx()
        top = self.right_panel.winfo_rooty()
        right = left + self.right_panel.winfo_width()
        bottom = top + self.right_panel.winfo_height()
        return left <= x_root <= right and top <= y_root <= bottom


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EAI Env DIY visual window")
    parser.add_argument("--keyboard-preflight-output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = tk.Tk()
    TaskDiyWindow(root, keyboard_preflight_output=args.keyboard_preflight_output)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
